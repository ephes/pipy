# Canonical-loop durability integration spike

Owner-authorized on 2026-10-19 after the [first prototype](durable-prototype.md).
The selected priority is controlled resume of **one native run after terminal or
process death**. This second experiment runs the real synchronous `AgentLoop`;
it is still a standalone experiment, with no production entry-point, default,
product-session migration or native runtime source change.

The owner later authorized the full
[durable execution work order](specs/2026-10-19-durable-execution-plan.md).
DE00 imports and reproduces this spike; the
[evidence ledger](specs/2026-10-19-durable-execution-evidence.md) records its
current gates. The feature recommendation below describes the experiment's
historical next-step scope, before the full campaign was authorized.

## Reproduce

Use a POSIX host and disposable, non-sensitive data:

```sh
scratch="$(mktemp -d /tmp/pipy-durable-loop.XXXXXX)"
mkdir "$scratch/workspace" "$scratch/evidence"

# Expected exit 86 after the canonical assistant and both tool admissions commit.
uv run python scripts/durable_prototype/loop_integration.py start \
  --db "$scratch/run.sqlite" --workspace "$scratch/workspace" \
  --scenario unsafe --crash-at g0.message
uv run python scripts/durable_prototype/loop_integration.py resume --db "$scratch/run.sqlite"
uv run python scripts/durable_prototype/loop_integration.py inspect --db "$scratch/run.sqlite"

uv run python scripts/durable_prototype/loop_experiments.py --output "$scratch/evidence"
uv run pytest tests/test_durable_loop_integration.py tests/test_durable_prototype.py -q
```

A separate database is required for each run. `start` deduplicates the same
request/configuration and rejects a conflicting admission. `resume` and
`inspect` require an existing database. Exit 0 means completed, 3 means blocked,
2 means invalid usage, and 86 is the deliberate hard-exit fault. A blocked run
stays inspectable and does not silently retry unsafe work. There is no operator
resolution command in this spike.

Useful cuts: `g0.receipt` (provider response stored), `g0.message` (canonical
assistant plus pending tool admissions), `t0.intent` (about to execute),
`t0.effect` (external effect without result receipt), `t0.receipt` (native
execution outcome stored before product projection), `t0.message` (canonical
result stored), and `settled.committed` (final state stored before observer).
`g1` is the final provider turn; `t1` is the native read. `--pause-at CUT --marker
PATH` creates a barrier for an external `kill -KILL`. `--fail-write-at
 t0.receipt` injects a real SQLite read-only write rejection;
`t0.receipt.after_commit` injects a lost acknowledgement after a successful
COMMIT. `--terminate` makes both tool results request termination, exercising
that canonical control field across recovery.

The SQLite DB contains full experimental input, provider responses, signatures,
arguments and results. Its owner-only file and persistent lock use the first
prototype's storage primitive; a single owner holds the OS lock across external
execution. No production session or workflow archive is a second authority.
Keep evidence outside git, protect its directory, and remove it after use.
Workflow capture records only summary-safe decisions and outcomes.

## Actual integration boundary

Every active resume starts **the real `AgentLoop.run()` from its accepted input**.
The loop reconstructs history, tool-budget transitions and usage by traversing
its ordinary control flow. Existing product adapters provide request snapshots,
provider turns, policy, native capabilities, usage and product-session projection:

- `AgentLoopRequestSourceAdapter` and `AgentLoopProviderTurnAdapter`, with
  `snapshot_provider_request`, `materialize_provider_request` and the real
  `ProviderTurnExecutor`;
- `NativeToolCapabilities` and its real `ToolExecutor`, calling native
  write/bash/read in the disposable workspace;
- `NativeAgentToolPolicy`, `NativeAgentUsagePublisher`,
  `NativeAgentQueuedInputPort` and `AgentLoopStatusPolicyAdapter`;
- `ProductSessionEventProjection`, `NativeProductSessionActionSink` and
  `SynchronousAgentEventComposite`, ordered persistence before observer.

Experimental adapters place I/O receipts around existing provider-turn and
execution-capability ports. A stored response returns the exact
`ProviderTurnOutcome`; a stored tool completion returns the exact
`ToolExecutionOutcome`, including the original pipy request ID. No second
provider/tool scheduler drives progress, no private `_RunState` is serialized,
and no core-loop resume branch was added. The first prototype remains runnable.

Provider results commit before returning into the loop. Product projection then
commits the fully normalized assistant and its pending tool admissions together.
A tool receipt commits before returning into result hooks and canonical
projection. The exact transformed canonical result commits before its observer.
The fresh loop recomputes usage and budgets; it does **not** add recovered totals
to an old accumulator. A terminal done/blocked run retains its persisted outcome
and drains already committed observations before returning, without execution
compatibility checks. Active execution still rejects configuration drift. A
conflicting `start` admission remains an error and cannot rewrite terminal state.

The admitted configuration pins provider/API/model, workspace path and
filesystem identity, tool plan, budget, pricing and hashes of native Python
sources plus the experiment's runtime/codec/storage files. Requests compare
serialized rich history, tool schemas, target-specific replay view, identity and
prompts; tool receipts compare call identity and prepared arguments. A mismatch
blocks before new external work. Missing results or receipts contradicted by
committed messages/admissions also block. This is conservative compatibility
checking, not a signed or tamper-proof database format.

## What publication means here

Only completed canonical messages and the settled summary reach the experimental
observer. Partial text/reasoning, tool progress, lifecycle/status events and live
usage are deliberately suppressed. The native usage adapter still reconstructs
its session accumulator, checked against the loop's final usage.

The observer opens a **separate SQLite connection**, reads the exact committed
message/summary and completed I/O receipt, and only then records its observation.
A negative test tries to publish a write still pending on the producer connection
and proves refusal. Stored observation keys make this particular observer
idempotent. After commit-before-callback death, resume drains pending facts.
A general external consumer could receive a callback again after an ambiguous
acknowledgement; exactly-once external publication is not established.

This does not exercise the production TUI, SDK stream, raw terminal lifecycle,
`CodingProductSessionCoordinator` or its in-memory mirror. Its existing
apply-before-persist callback is bypassed by the experimental action sink; no
second product JSONL writer is installed. The result is evidence for a headless
completed-message boundary, not a claim that existing product streaming is now
durable.

## Measured evidence

The standalone matrix passed **46 cases**: two baselines, 32 hard-exit cuts, two
real SIGKILL effect cuts and ten SQLite rejection/acknowledgement-loss cases.
Every recovery also repeats resume and asserts exact attempt counts, effect
lines, observer facts, canonical/session usage equality and SQLite integrity.
An independent read-only worker validated eight recovery scenarios, including
three additional actual SIGKILLs at assistant admission, unsafe tool receipt and
settled commit. Those overlap the matrix and are not eight distinct new features.

| Boundary | Observed outcome |
| --- | --- |
| Baseline | 2 provider attempts, 2 native tool attempts, 5 canonical messages, 6 unique observer facts, 1 effect line |
| Canonical assistant plus tools admitted | Provider result reused; both pending tools run once |
| Native tool receipt committed before projection | Tool not repeated; original request/correlation IDs and rich result projected |
| Canonical result committed before observer | Stored result published on reopen; no repeated external effect |
| Final assistant or settled state committed | No extra provider call; completed state and pending observation recovered |
| Unsafe intent interrupted before execution | Blocked despite 0 effect lines; later read remains admitted with 0 attempts |
| Unsafe effect interrupted before receipt | Blocked with 1 line and no manufactured result; repeat resume stays blocked |
| Safe overwrite interrupted | Explicitly safe call retries, final file has 1 line; this is not general `write` replay safety |
| Interrupted native read | Read retries; prior committed effect tool stays at 1 attempt |
| Provider interrupted during stream / after response before receipt | First request is reissued; 3 total provider attempts versus baseline 2 |
| Canonical assistant commit rejected | No tool execution; provider receipt is reused after reopen |
| Tool receipt write rejected after effect | Safe retry or unsafe block, depending on stored policy |
| Tool receipt committed but acknowledgement lost | Open store fails closed; reopen reuses the receipt and does not append twice |
| Final state committed but acknowledgement lost | Reopen returns completion and drains observation without external repeats |

Completed runs reconstructed identical loop and native-session totals:
input **200**, output **40**, reasoning **14**, cache read **60**, cache write
**20**, calculated fixture cost **0.000261**. These are fixture token/cost values,
not measured provider billing. Native canonical message usage stores **60
uncached input** per response, whereas the raw provider receipt retains input
**100**, cache read **30**, cache write **10**, and total **127**. Assertions cover
both forms instead of treating that expected normalization as data loss.

Exact field checks use the finite checkpoint JSON, not dataclass equality:
usage, provenance, details and other important fields use `compare=False` in
native types. Recovered messages preserve ordered thinking/redaction, thinking
and text signatures, tool thought signatures, whitespace in raw argument JSON,
provider/API/model/thinking level, usage/cache-write-1h/cost, original tool IDs,
image-block DTOs, nested details, structured content and `terminate`. The image
is synthetic fixture data; decoding or rendering it is not tested. Native replay
projection retains signatures for the exact target and strips foreign thinking
and signatures for a changed model. Completed reads keep their stored result
even if the file changes later. With stored `terminate=True` on both calls, the
real loop stops after that batch without a final provider call after recovery.

Attempt counters are execution-intent admissions, not universally proof that a
network request happened: a process may die between intent and invocation. At
the stream/response barriers a provider invocation did happen. No live-provider
cost, server-side completion or remote cancellation guarantee follows.

## Findings and residual boundaries

The smallest demonstrated boundary is **receipt replay around existing loop
ports plus commit-before-completed-message observation**. No production loop
change was necessary. Compared with the first prototype, this now proves actual
canonical policy, history and usage reconstruction, and real product projection
ordering for this narrow sink. It also exposes why the transcript serializer is
insufficient for checkpoints: it intentionally omits structured content and
termination, which the experimental finite codec retains.

Canonical control flow, request preparation, policies, normalization, status and
queue callbacks rerun on active resume. Only this fixture's pure/pinned callbacks
are supported. Arbitrary extension hooks, dynamic tools, resource loading,
changed model catalog/pricing, compaction and other product effects cannot be
assumed safe to replay. Workspace inode pinning is not a file snapshot; a fresh
read can observe changed files, and overwrite replay assumes exclusive workspace
ownership. Native source hashes deliberately refuse upgrades rather than migrate
in-flight work; they do not invalidate terminal evidence or strand a committed
observation after completion. Long transcripts would require evaluating replay cost or a
more precise checkpoint boundary.

Not proved: live provider wire/billing/retry/cancellation, terminal disconnect
handling, TUI/tree/SDK compatibility, machine power loss, physical disk-full or
I/O corruption, Windows/shared filesystems, noncooperating writers, active child
processes outliving a killed parent, or operator resolution of uncertain effects.
This is one explicitly resumed run, with no automatic restart, subagents,
concurrent tools, existing-session migration or Python codemode continuation.
External effects still cannot transact atomically with SQLite; unsafe ambiguity
is exposed rather than hidden.

## Feature recommendation for the selected priority

The integration spike is complete. A reasonable next feature scope is an
**opt-in headless new-run mode with explicit resume and completed-message
recovery**, using one authoritative store. Retain stable receipt IDs, fail closed
on identity/configuration drift, reuse committed results and expose unsafe
interruption as blocked. Start with fixed native tools and replay-safe/pure
callbacks; arbitrary extension hooks need a separate contract. Keep existing
product sessions and defaults unchanged, and make any transcript export a
derived view rather than a second authority.

The earlier **2–4 engineering-week** estimate remains a low-to-medium-confidence
range for that bounded feature, including real-provider failure/cost evidence,
privacy/storage lifecycle, compatibility checks and a usable uncertain-outcome
inspection contract. The experiment reduces uncertainty about the core loop
seam; it does not size durable live streaming, TUI/tree integration, cancellation,
subagents or Python codemode. Production feature implementation is a subsequent
scope decision, not part of this authorized integration spike.
