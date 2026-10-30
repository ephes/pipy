# DE15b — bounded ordinary-owned execution and completing

## Historical source checkpoint

The initial private source implementation used the accepted d16/4374 epoch. PLAN R3 was
independently CLEAN with Root adoption c75e67a0. This source-authoring checkpoint
claims no runtime, reference comparison, CODE acceptance, integration or delivery.
All DE14/DE15/DE16 and 43 parent parity criteria remain open.

## Implemented scope and validation

The implementation supplies the bounded ordinary dispatch/completing behavior
below. Joint tree `4b088507` passed 709 focused tests and current configuration,
coverage and strict-doc checks; whole quality 1147/1279 belongs to its Python-equal
4b075 predecessor. The whole migration module subsequently passed 62 on 3339.
The reserved-lineage fixture/spec clarification passed 27 on 4456 and received a
separate CLEAN test-repair CODE review; production bytes were unchanged. Combined
b06 passed that same 27-case module, whole mypy 1151/Ruff/format 1283 and current 34
configuration/coverage/strict-doc checks. These are distinct accepted epochs,
not a cumulative test count or a combined Full result. DE15c waiting and DE16a
leaf abort add their own bounded consumers; full DE15/DE16 and all 43 parent
criteria remain open.

## Authority and coherent graph

The delivered OwnedLiveQuery.capture(SyncTx).has remains exact and still refuses
AsyncTx. Its typed projection, complete strict ascending paged reads, global
acyclic task/conversation topology, real core create/replace candidate identity
and ordinary traversal are shared with one private awaited AsyncTx consumer.
Capture precedes writes; repeated answers overlay actual same-Tx candidates. No
caller maps, caller done sets, persistent cache, new query API or read-after-write
bypass are introduced. Task inputs/checkpoints/memos are not retained in graph
projections. The scheduler's private capture additionally retains only cancellation
intent booleans for current rows; it reevaluates genuine staged rows on the line.

Healthy ordinary generic tasks can now dispatch and make commit/memo progress.
Ordinary classification uses the task's own kind even under reserved ancestry; the reserved task itself still refuses live descendants and may regain eligibility after their genuine terminal settlement.
Exact reserved generation/model-tool paths still require their original graph,
receipt, slot and current-token checks. The activation condition is exactly any
row in the complete existing task scan whose kind is neither the existing exact
pi.generation nor pi.tool built-in name; wholly built-in transactions keep their
previous graph route. User-defined pi.user-* kinds remain ordinary: no reserved
prefix or new Task-name policy is introduced. This is not a fitting-definition whitelist: once ordinary capture is
needed, all mixed task/conversation rows are validated, including unknown, blocked
or uninvokable ordinary descendants, which continue holding their owner.
Missing/older/incompatible code, parking and own abort marks retain existing refusal. Orphan root-leaf admission is not
replaced by an ordinary-live false answer.

A completed terminal intent holds as completing while live ordinary owned work
remains; terminal intermediates are traversed and background subtrees excluded.
The phase retires before the next sequential phase runs. A bottom-up fixed point
on the real Session line finalizes generic held outcomes whose ordinary work is
gone, independently of tool registration. Metadata-only recovery may reconcile
held outcomes but never runs a handler. Normal _state clears memos/restarts when
an outcome is decided (completing or terminal); actual terminal assembly alone
retires task docs under existing Memory/SQLite history rules.

Existing TaskTx.create_task is reused. In particular the same candidate that
creates a new child and makes its owner completing still refuses final-owner
assembly; its deliberate mint and durable rollback are separate facts. A normal
workflow creates the child in an earlier running progress commit. No Tx/backend
owner validator or public SDK/CLI contract is widened.

## Explicit cancellation refusal, not DE16 cascade

Literal Pi4a08 cancellationIntent is nonterminal plus abortRequested or a held
outcome other than completed. Fresh generic dispatch and actual gated commit/memo
refuse when that intent reaches them through ANY foreground ownership ancestor,
including through healthy or terminal intermediates. Outside intent stops at a
background boundary; a background owner's own intent still applies to its
foreground children. The same validated captured graph/candidate authority decides
all these checks; no descendant abort marks or pretend terminal outcomes are added.

A phase fault can be held with the existing outcome.status=faulted. Its children
do not run normally; the owner stays held until ordinary descendants actually
settle through supported authority. Full cascade/active invocation interruption
remains DE16. Already admitted off-line work is not undone or falsely called
cancelled: cleanup stays physically joined. Subsequent gated writes refuse.
The _step inherited-intent branch precedes BOTH error and no-progress handling,
returning the child to pending with identical checkpoint/restarts. Re-raised and
swallowed InvocationEnded therefore cannot manufacture a faulted outcome.

The existing Session condition still guards invocation/enabled/closing/index and
immutable generation publication. Graph snapshots remain transaction-local; no
backend awaits, callbacks, future completion, joins or old reference disposal move
under that guard. Physical Session close remains the lifetime owner.

## Witness scope

test_owned_completing.py uses actual Memory/SQLite Session/Tx and sequential
phases for healthy child memo+commit, completing/doc holds, cold checkpoint
recovery with no tool definition, later task pages/terminal intermediates, distant
ancestor refusal/background cuts, real phase fault, re-raised and swallowed
gated refusals, exact terminal-intent validation before holding, real pre-COMMIT rollback versus COMMIT-before-ACK-loss, and
cancelled close waiter/held finalizer/real independent opener. Existing actors
and nine-table/revision oracles are reused. The old live-child runtime test keeps
its ID but now witnesses the positive completing hold instead of blanket refusal.

No new reference profile, recipe case, Pi pin, phase ID or parent edge is added
here. Existing memberships are preserved; only necessary source closure consumers
and digests follow this code. The accepted native checks above retain their
actual source epochs; they do not establish a new paired profile or parent
acceptance.
