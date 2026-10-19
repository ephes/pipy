# Durable native run prototype

This is an isolated learning experiment, authorized 2026-10-19. It does not
change `pipy`, the SDK, product sessions, or workflow capture. It is not a
production durability implementation or an approved migration plan.

The owner later authorized the full
[durable execution work order](specs/2026-10-19-durable-execution-plan.md).
DE00 imports and reproduces this experiment; the
[evidence ledger](specs/2026-10-19-durable-execution-evidence.md) records its
current gates. The recommendations below retain the experiment's historical
scope and estimates. They do not establish production or upstream parity.

## Run it

From the repository root, with `uv` and a POSIX host:

```sh
scratch="$(mktemp -d /tmp/pipy-durable-demo.XXXXXX)"
mkdir "$scratch/safe" "$scratch/unsafe"

# Expected exit 86: the native write finished, its result did not commit.
uv run python scripts/durable_prototype/runtime.py start \
  --db "$scratch/safe/run.sqlite" --workspace "$scratch/safe" \
  --scenario safe --crash-at tool.0.effect
uv run python scripts/durable_prototype/runtime.py resume --db "$scratch/safe/run.sqlite"

# Expected exit 86, then exit 3 (blocked): the native bash append may have run.
uv run python scripts/durable_prototype/runtime.py start \
  --db "$scratch/unsafe/run.sqlite" --workspace "$scratch/unsafe" \
  --scenario unsafe --crash-at tool.0.effect
uv run python scripts/durable_prototype/runtime.py resume --db "$scratch/unsafe/run.sqlite"
uv run python scripts/durable_prototype/runtime.py inspect --db "$scratch/unsafe/run.sqlite"

# All reproducible cases, retaining per-process stdout/stderr and SQLite files.
uv run python scripts/durable_prototype/experiments.py --output "$scratch"
uv run pytest tests/test_durable_prototype.py -q
```

`start` admits one input per database; repeating the same request ID and exact
configuration reuses it. A different request, prompt, scenario or workspace is
rejected. Invalid usage exits 2, separately from blocked exit 3; invalid workspace
arguments and nonexistent resume/inspect paths create no DB or lock. `resume`
needs only the database. `inspect` prints counts and task
phases, with exit 3 for blocked runs. A blocked run stays blocked across resumes;
there is deliberately no recovery-decision command. Inspect its disposable
workspace and start a separate experiment after deciding what to do. Do not edit
the DB to pretend an uncertain operation succeeded.

The script persists full prompt, tool arguments and text results in this
explicitly selected experimental database. Use disposable, non-sensitive data.
The DB and lock have owner-only permissions; `mktemp -d` protects the evidence
directory. Neither the database nor raw experiment output belongs in git or the
metadata-only workflow archive. No production session is imported or migrated.

## What is implemented

The runtime is Python and uses pipy's real `ProviderRequest`, canonical
`AgentUserMessage` / `AgentAssistantMessage` / `AgentToolResultMessage`,
`FakeNativeProvider.complete()` and stream callback, native argument validation,
and actual `WriteTool`, `ReadTool` and `BashTool`. The offline provider is rebuilt
from the persisted generation position on each request. Both scripts are
standalone files under `scripts/durable_prototype/`; production entry points do
not import them. No network provider or credentials are required.

One conversation has one admitted input, sequential generation/tool tasks, and
a final answer. SQLite is the sole experimental source of truth. Admission
inserts the input and first pending generation together. A completed generation
commits its assistant entry and pending tool together. Tool intent becomes
`running` before native execution. Its result, completed task and next pending
generation commit atomically. Unique task/entry identities prevent duplicate
committed results. Restart reconstructs canonical provider messages from those
entries, including both pipy request IDs and provider correlation IDs.

Pending tools have not executed. Interrupted running tools replay only if their
stored **and** current policy allow it. Otherwise the tool becomes `uncertain`
and the admission becomes `blocked`, without a manufactured success result or
another provider call. The safe scenario overwrites one fixed file with fixed
bytes, then reads it. The unsafe scenario appends a line through native bash,
then reads it. Only that particular overwrite under exclusive workspace
ownership is declared safe; this does not classify general native `write`,
`edit`, bash or external tools as replay-safe. Even replayed reads can observe
changed files; completed read results are reused from storage.

A nonblocking OS `flock` on a persistent sidecar owns the entire run, including
external work. A concurrent opener fails before admission or execution. SQLite
uses `synchronous=FULL` and the DELETE journal. Any transaction exception poisons
the open store: further execution requires reopen. Output is a final counts-only
summary after commits. Stream deltas and tool progress are not published or
persisted. Thus a generation interrupted during streaming is reissued from the
last complete history; partial text is lost.

## Observations on 2026-10-19

The reproducible matrix passed **33 cases**: two baselines, twenty `os._exit(86)`
crash cuts, two real `SIGKILL` cuts, eight injected SQLite failure cases, and one
concurrent-open probe. Every recovery case also resumes a second time and
checks an unchanged summary, expected attempt/effect counts, unique committed
results and `integrity_check`. Optimized Python (`-O` / `PYTHONOPTIMIZE`) is
refused so it cannot silently disable the driver assertions. The two named cuts
`generation.0.committed` and `tool.0.admitted` deliberately probe the same atomic
assistant/tool admission; the twenty cuts do not represent twenty distinct
durable states.
An independent read-only worker passed **12 additional probe groups**, partly
overlapping the matrix, including policy intersection and pinned arguments.
These counts are evidence runs, not counts of distinct behaviors.

| Cut / condition | Measured recovery |
| --- | --- |
| Baseline | 3 provider attempts, 1 attempt per tool, 2 committed results, 1 effect line |
| Admission committed, before execution | Input/task retained; both scenarios finish with baseline counts |
| Tool pending after assistant commit | Both scenarios invoke it once and finish |
| Tool `running`, before native invocation | Safe tool has 2 intent attempts and finishes; unsafe blocks with **0 effect lines**, 0 results |
| After external effect, before result commit | Safe overwrite has 2 attempts, 1 final effect line, 2 results; unsafe blocks with 1 line, 0 results |
| Real `SIGKILL` at that effect barrier | Same safe/unsafe outcomes; OS owner releases on death |
| Tool result committed, before publication | 1 attempt for that tool; stored result reused, no repeated append |
| Interrupted second tool (read) | Read retries once; earlier committed write/bash stays at 1 attempt |
| During first generation stream / after response but before commit | 4 provider attempts instead of 3; no partial assistant/call admitted |
| First generation committed, before publication | 3 provider attempts total; assistant/tool admission reused |
| Final answer committed, before publication | Reopen returns done without another provider request |
| SQLite admission / tool-start write rejected | No effect before successful admission/intent; clean reopen proceeds |
| SQLite result write rejected after effect | Safe retries; unsafe blocks; no publication on failing owner |
| Result committed but acknowledgement lost | Failing owner publishes nothing; reopen finds result and does not repeat effect |
| Concurrent opener during native-effect barrier | Rejected with `BlockingIOError`; after killing owner, reopen blocks unsafe call without appending again |

The independent probes also demonstrated: stored-safe/current-unsafe and
stored-unsafe/current-safe both block; replay uses persisted arguments even if
the current script's arguments change; lost admission acknowledgement dedupes
on the same request; and native message/result correlations survive reopen.
Focused tests additionally pin reuse of a committed read even after its file
changes and refusal to keep running a poisoned store.

An `attempts` counter records a durably admitted execution attempt. A process
can die between that commit and the actual provider/tool invocation, so this is
not universally a network-request or external-effect counter. At the measured
stream/response barriers the provider was actually called twice. With a paid
provider, duplicate billing, provider-side completion and remote cancellation
would be uncertain; this offline experiment makes no billing guarantee.

## What this teaches us

The native value objects and concrete tools work without TypeScript wrapping.
Admission/result atomicity and a single owner are enough for this bounded
sequential recovery experiment. The unsafe crash window is fundamental: a
filesystem mutation and a SQLite result commit are different transactions.
Exactly-once external effects require a tool-specific idempotency or receipt
protocol, or an explicit uncertain-outcome decision. Conservatively blocking
also sacrifices progress when intent committed but execution never started.

Current production sessions are transcripts, not persisted task schedulers.
Wrapping their append sink would not establish commit-before-visible behavior:
the existing agent has live partial/tool updates, and product-session
coordination includes in-memory changes before persistence. Production adoption
would need deliberate ownership of admission, execution, persistence and
publication. This prototype therefore does not modify the product loop or
write a second authoritative product store.

The reference was local Pi `2ec55d808`, particularly
`packages/durable/src/harness/tool.ts` (stored/current replay-policy
intersection), `generation.ts`, `scheduler.ts`, SQLite storage and
`codemode/codemode.ts`. Pi has a much broader task/entry/document model and can
return interrupted-tool error results to the model. This prototype instead
exposes a durable blocked state. Pi retains committed partial generations;
this experiment buffers and loses partials. It is not Pi parity evidence.

## Boundaries and decisions left open

Untested: live provider failures/costs, physical disk-full or I/O loss, machine
power loss, Windows, network/shared filesystems, noncooperating writers, and
long-running subprocesses surviving their parent. The SQLite failures here are
real read-only write rejections plus an injected exception **after** successful
COMMIT, not a simulation of every ambiguous filesystem failure.

There is no generic task framework, concurrent tools, queued submissions,
cancellation/retry policy, leases across machines, migration/version upgrade,
compaction, images, rich provider content, extension/MCP replay registry,
operator resolution UI, or codemode continuation. The narrow text codec does
not preserve signatures, usage or rich result details. There is no automatic
restart loop; every resume is explicit.

Arbitrary Python codemode cannot resume from a serialized program counter here.
Reexecuting a script requires deterministic input, child-call identity, stored
results and delivery ordering, divergence detection, pinned code/tool versions,
and coordinated codemode state writes. Neither this prototype's safe overwrite
nor a persisted parent result solves those requirements.

Evidence supports a later decision on the smallest useful product recovery
boundary, handling unsafe uncertainty, retaining partial output, workspace and
provider identity, state/version pinning, and how private transcript storage and
task state would share one authority. These are questions to resolve from user
needs after the experiment, not an implementation backlog committed by this
prototype.

## Recommended next step

The owner authorized this integration spike on 2026-10-19; it is now completed
as a [real canonical-loop experiment](durable-loop-integration.md). The
recommendation below records the decision that led to it; the linked findings
own the current feature recommendation.

The owner selected controlled resume of one native run after a terminal/process
crash as the priority on 2026-10-19. The next useful experiment is a **2–3
focused-day integration spike** through the real
canonical loop and product adapters. Exercise a rich reasoning/signature/usage
fixture with tool correlations and kill after assistant admission and tool-result
commit. The present prototype reuses their value objects and ports; it does not
yet prove durable ownership inside that loop.

If that spike confirms a small integration boundary, an opt-in headless feature
looks like **2–4 engineering weeks**, at low-to-medium confidence. Keep one
authority, unsafe interruption blocked, and identity/configuration pinned; decide
whether completed-message recovery suffices or streaming must be retained. TUI
and tree compatibility are not estimated, and subagents and Python codemode
remain separate work. These estimates are a recommendation based on the current
evidence, not authorization to implement a production feature.
