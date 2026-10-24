# Durable generation (DE07)

Implemented against delivered DE06b `69812fa5`. This private native
generation definition uses the existing Session line, canonical envelopes and
provider construction/auth owners. It does not activate a product or CLI path.

Preparation reads a supported committed conversation cutoff, freezes its exact
request and advertised tools, and establishes the conversation's durable provider
session identity before transport. Saved construction semantics and pricing win
current defaults. Recovery from request uses that committed request; recovery
from a response receipt performs only settlement. A request interrupted before
its receipt may have been paid and may be reissued. There is no exactly-once
provider claim and no durable partial-stream claim.

Provider work runs outside the Session line. A durable-only executor retains its
real provider threads; the phase finalizer joins actual wrapper and worker
lifetimes before the existing lifecycle can close storage or release its lock.
Cancellation or a bounded legacy executor join cannot release ownership. Close
may remain pending for a noncooperating transport.

The executor uses the existing caller-managed allowance of one logical attempt.
`retry_policy=None` would invoke Codex's standalone retry loop, so prepared ports
must receive `ProviderAttemptAllowance(1, 1)` through the existing executor seam.
This does not claim one wire connection: adapter-owned transport fallback can
still reconnect within that attempt. Durable attempt retry/backoff remains DE17.

The response receipt retains the original outcome, request and completed rich
assistant in canonical arenas. One settlement commits assistant, saved-price
usage exactly once, actual owned ToolTasks or unavailable-call results, ordered
round state and terminal/waiting generation state. Completed observations come
only from committed Session publications. No legacy state-first projection or
independent JSONL writer participates.

Default rounds atomically admit **all** offered calls as real `pi.tool` version2
tasks with `{kind: "model", assistant, callId}` and the generation as owner.
This matches pinned Pi's default admission, independently of DE08's sequential
execution limit. Explicit sequential-round policy has different staged admission
upstream and is unsupported here. TaskTx adds only current-conversation reads
and current-owner child creation. Waiting admission is restricted to the actual
new generation children and `allSettled`; terminal live-child refusal remains.

The original DE07 admission token holds rounds without execution. Registering
[DE08's real tool definition](durable-tools.md) enables direct owned-leaf
eligibility, tool intent/result/safety, captured-parent wait reconciliation on
the Session line and fresh-request generation continuation. DE15 still
owns general graph/wait/cycle/completing/background semantics. Retries/deferred
handles remain DE17, committed partials DE26, and product projection DE09–12.
All original DE07/P15/P16/P17/P19 acceptance remains; all43 parity rows are OPEN.

Required evidence includes real Memory/SQLite/reopen, process death and ack/fault
cuts around request/receipt/settlement, independently counted effects and
commit-before-observer, atomic child rollback, rich data/pricing fidelity and a
held real provider worker proving lock ownership through close. The
[acceptance ledger](specs/2026-10-19-durable-execution-evidence.md#de07-generation-acceptance)
records executed evidence; final delivery requires all mandatory review,
full-check, platform and reference gates.

## Private interface and recovery

`define_generation(spec, auth_path=..., env=..., ...)` captures fixed native
construction inputs and returns the ordinary `pi.generation` version1 Task.
Create it through Session Tx with `encode_canonical(request_snapshot)` as input.
The seed supplies supported request policy fields; preparation replaces history
with committed model entries from this conversation only. `message_entry(dto)`
encodes the supported user/assistant/tool-result model entry representation.
Edits, head changes and other context semantics refuse here; full derivation is
DE20. No caller callback/provider factory or sequential-round control is admitted.

The conversation's first provider ID is generated durably. A supplied session
ID must already match that conversation's persisted winner; callers cannot give
two new conversations the same cache identity. The original cache-retention
field, including None, remains intact; saved effective adapter defaults are
separate. Private material allocation uses the durable memo winner. If preparation
was interrupted after protecting material but before request adoption, a changed
binding refuses instead of overwriting that immutable material.

`request` reconstructs from the committed envelope and rechecks the current
running checkpoint/abort gate before transport. `settle` decodes the stored
outcome and receipt document without credentials. The original response/request
remain on the committed assistant entry after task-scoped receipt retirement.
Completed usage is calculated with the saved rates/tiers, including cache1h and
reasoning; the conversation ledger couples each request settlement to its entry
and adds its spend once, including failed completed responses. This is the
bounded generation ledger, not the full partial/tool/compaction/fork sums of
later slices.

Tool round admission records every real child in call order and each unavailable
call's actual result entry. The runtime verifies its newly staged assistant and
all offered call inputs, exact owner/conversation/version, complete wait list and
retained receipt before admitting waiting. ToolTask has its actual version2
initializer and no executable phases yet; there is no success/no-op placeholder.
Owned children dispatch only with the explicit [DE08 tool definition](durable-tools.md);
the original admission token remains non-executable.

The comparison phase `generation-b-v1` runs actual pinned Harness generation,
transactions, usage and atomic admission on SQLite, using the fixed streamSimple
provider-fixture seam from pinned generation tests. Actual pi-ai calculateCost
prices that declared response. It compares only completed admission, identity,
usage/model ledger equality, actual close reason and explicit terminal-answer
resume/retention views with native
Memory/SQLite/reopen. IDs become explicit relation checks, and raw source/native
records are retained. It does not label native process or held-thread probes as
Pi observations, nor claim reference Memory execution in this child phase.


The paired answer case explicitly resumes terminal work and waits for actual
scheduler idle/drain before measuring retained state and calls. SQLite/reopen
use a new owner, so that drain follows explicit resume. Memory calls resume on
the current owner and observes a post-terminal empty drain; the prior terminal
notification may cause that drain, so it does not establish resume causation.
The tool case
stops at admission and opens without resume because its execution/wake behavior
belongs to DE08. Intermediate receipt recovery is proved by native real
crash/acknowledgement tests, not claimed as a paired Pi receipt checkpoint.
Successful close is observed through each actual owner's close reason; held
provider-thread/finalizer safety remains native executable evidence.

## Request snapshot retention debt (DE27/28)

Each settled assistant entry currently retains its exact request snapshot,
including the history through its cutoff. Across many generations this duplicates
message history and produces quadratic storage growth. This is an explicit
limitation of the unactivated private foundation, accepted for DE07 rather than
changing the persisted fidelity/authorization contract during evidence repair.
DE27/28 must replace or bound this representation with a versioned format and
migration/retention rule, preserving exact cutoff/tool authorization and terminal
inspection without credentials. Their acceptance must include multi-generation
stored-byte growth tests and retained-old-format recovery. Full-feature
acceptance must resolve this debt or record an explicit justified storage budget;
it cannot disappear merely because DE07 is complete.


DE08 preserves each closed round's committed slot order for later context reads,
including unavailable results physically written before other calls complete.
Every new prepare has a distinct seed/request/attempt and per-request material
memo; recovered request/receipt phases keep their original identity. Native
currently advances its same parent, whereas pinned Pi creates a successor
generation task. [The bounded graph difference](durable-tools.md#logical-round-and-graph-boundary)
remains with DE15/28 before full exposed ownership/graph parity is accepted.

[DE09 completed snapshots](durable-completed-projection.md) expose settled
assistant/provider outcome payloads and committed usage without reconstructing
current credentials or repricing. Outward DTOs exclude original saved requests,
settings and private material pointers. Whole generation terminal outcomes and
logical closed rounds remain distinct; waiting/pending control holds are not
reported as completed parents. Public product activation remains DE11/12.

## Inspection and explicit new generation attempts (DE10)

Factories capture finite private support metadata once, separate from live
handlers/auth/material. [Inspection](durable-inspection.md) may compare known
provider/model/API and format fit without reconstructing a provider. Saved
thinking/options/pricing stay authoritative for an existing prepared request;
changed defaults do not reprice or rewrite it. Actual reconstruction still checks
private target/environment/cache/header semantics and live credentials before
effects. Material availability is not probed merely to inspect.

An explicit operator retry supports only eligible ownerless failed/faulted/
aborted terminal generation with no live descendants and a new canonical seed.
It admits fresh request/attempt identity without changing the old terminal row.
Pending blocked terminate is not retryable. Product activation, automatic retries,
full graphs/controls and request-history/publication budgets keep their later
owners; this does not close the Pi successor-task/native same-parent difference.
