# Python codemode: light plan

Status: queued as CM1 on 2026-10-04 at the owner's request. The isolation
spike finished on 2026-10-06; see
[the spike result](2026-10-06-python-codemode-spike.md). Direction and
first-slice boundaries, not an implementation-ready sandbox specification.
[Backlog](../backlog.md) owns selection and order.

## Goal

Let the model compose existing pipy tools and reduce intermediate results
in Python within one model turn. For example:

```python
result = tools.read({"path": "pyproject.toml"})
text(result)
```

This is proposed syntax, not an existing API. Scripts use Python rather than
Pi's JavaScript; script compatibility with Pi is not a requirement. Adopt
Pi's useful composition behavior while keeping pipy-native and the
synchronous agent core. MCP is not a prerequisite.

## First step: feasibility spike

Evaluate a disposable worker process plus an enforceable isolation backend.
A subprocess provides crash isolation and termination, but is not itself a
security boundary. Restricted built-ins, import filters or AST checks alone
are also insufficient. Do not ship ordinary unrestricted `exec()` as a
capability-limited sandbox.

Compare viable backends for development macOS and intended supported
platforms. Establish what they can deny: direct filesystem access, network,
process spawning and access to credentials. Host effects must pass through
injected tools. Explore required dependencies, packaging, startup overhead
and how unavailable isolation fails closed. A reduced Python runtime is an
alternative to investigate if full CPython isolation proves impractical.

Deliver a small disposable experiment, focused escape/termination probes,
and a backend/platform recommendation. Record unsupported cases explicitly;
do not silently fall back to an unrestricted interpreter. The implementation
slice starts only after choosing an enforceable boundary and documenting it.

## Proposed first useful slice

- Opt-in `codemode` tool, initially with a normal JSON `code` string argument
  for provider portability. Raw-source provider support is a later decision.
- Sequential calls to eligible built-in tools through `tools.<name>(args)`.
  Keep directly exposed tools available; disallow recursive codemode calls.
- `text(value)` for explicit output, bounded arguments/results and a clear
  script error result retaining output produced before failure.
- Fresh worker per script, wall-time and memory limits, and operator
  cancellation that propagates to the active host tool and reaps workers.
- Nested calls retain validation, authority, interruption handling, tool
  budgets and observable lifecycle records. Use the session's admitted tool
  surface; no direct executor shortcut bypassing agent policy.

Already completed tool effects are not rolled back when a script fails.
Avoid automatic whole-script retries after effects; cancellation and retry
ownership belong in the implementation contract. No persistent script store
in this slice, so resume/tree behavior requires nested-call evidence but no
new branch-state subsystem.

## Integration boundaries

The current agent loop owns policy settlement, tool events and transcript
writes around each call (`native/agent/loop.py`). Extract or expose a bounded
nested-call service that preserves that ownership. The worker sends tool
requests over a narrow protocol; the host executes approved calls and sends
serializable results. Python worker code cannot mutate session/extension
state directly.

Before implementation, settle ownership of nested IDs/events, budget
accounting, interruption propagation and session persistence. A parent
script must not disguise calls or lose their outcome on failure. Pin concrete
ordering and delivery behavior in tests when implemented rather than
specifying a broad new event framework here.

## Acceptance evidence

Prove that a script reads multiple files, filters their contents and emits a
compact result through real built-in tools. Test tool validation/policy
failures, script failure after a completed effect, cancellation during a
tool, infinite-loop termination, memory/output limits, and worker cleanup.
Include negative isolation probes for filesystem, networking, imports that
escape the boundary, child processes and inherited credentials. Verify
headless/TUI lifecycle and durable resume/tree evidence for nested calls.

Compare request size, wall time and output quality against direct tools on
the same task; sequential composition primarily saves model round trips
and intermediate output, not tool execution time. No performance target
is chosen without measurements. Update user docs and release notes, run
focused tests and repository checks, and obtain independent review before
landing implementation.

## Current delivery split

T1–T6 provide sandbox and internal loop/service integration. T7a adds canonical
nested lifecycle events and live JSON/RPC/TUI projections, with numeric-only
workflow counters and parent-only persistence. Live child evidence is bounded
and remains inside one parent row. T7b implements the bounded durable nestedCalls
parent record, script-error/abort/timeout persistence, resume/tree reconstruction
and attempted file-operation input for compaction and branch summaries. Canonical
pipeline exceptions expose unfinished live/event evidence without a saved-session
guarantee. Ordinary direct-tool JSON/RPC shapes stay unchanged. T8a adds the
stable internal builtin definition, root runtime management CLI
and composite result truncation/spilling. T8b implements public enablement,
Pi `defaultTools` layering/CLI exact modifiers,
availability-gated registration and frozen reload selection. Slow probes and
concurrent probe waits run outside the shared session lock; cached availability
and one-warning ownership are synchronized. Diagnostics run outside coordination
locks through the transcript notice sink or headless stderr. T9 now supplies
deterministic public production delivery, product hook/abort coverage and
full-product PTY replay/tree assertions, plus an isolated terminal capture driver.
Selected-frame tmux inspection is recorded in the
[acceptance note](2026-10-09-python-codemode-acceptance.md); measurement and final
docs/cumulative review remain T10–T11.

## Deferred

Parallel execution and shared-state contracts; MCP/discovery/tool exposure;
extension-tool generalization; branch-aware `store()`/`load()`; image output
and generation; classifiers; JavaScript compatibility; codemode-only tool
loadouts. These require separately selected slices.

## Effort and open decisions

Treat this as a medium planning/spike item. A shippable initial runtime might
take roughly one to two engineering weeks after the isolation choice, but
Python sandbox feasibility and platform packaging could increase that
substantially. This is a rough sizing estimate, not a delivery commitment.

Open decisions: backend/platform support; accepted Python subset and imports;
worker protocol and serializable result shapes; budget accounting; nested
event persistence; default limits and output overflow handling. CM1's output
is the evidence and bounded implementation plan that resolve these choices.
