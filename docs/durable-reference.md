# Pinned durable reference verification

The verification tools execute Pi durable **1.3.0**, commit
`2ec55d808d455b1a64a2bf7888198207a096120a`, tree
`b51be844ffaf57e96d06a58d3f45ee90c6ba7401`. DE01a introduced actual Session,
MemoryStorage and document execution; landed DE01b1 added real faux-provider/tool
and context-head scenarios. Landed DE01b2 added SQLite recovery, durable abort and two
exported Node environment scenarios. These verify reference reproducibility and the oracle.
The full Python durable Storage/Session comparison adapter is **OPEN**, as are all
P01–P43 rows. The separate DE02a1 value/record comparison below now imports native
value DTOs; the six-family DE01 scripts remain verification-only. Product code
never imports a reference runner, and no native durable runtime is integrated.

## Reproduction

Use a POSIX host with Git, tar, **Node v26.12.0, npm 11.20.0**, supported Python,
registry access and a local Git checkout root containing the exact pinned
commit. These runtime versions are deliberately stricter than upstream's Node
>=22.19.0 minimum. No provider credentials, login, paid model or CLI wrapper is
used. Run with normal caller HOME; the runner creates a private synthetic child
HOME/cache/npm configuration and does not propagate provider or Node injection
variables.

```sh
scratch="$(mktemp -d /tmp/pipy-durable-reference.XXXXXX)"
uv run python scripts/durable_pi_comparison.py reference \
  --pi-source /Users/jochen/src/pi-mono --output "$scratch/reference"
uv run pytest tests/durable/test_pi_comparison.py \
  tests/durable/test_pi_comparison_cases.py -q
node tests/durable/test_reference_codec.mts
uv run python scripts/durable_pi_comparison.py compare \
  tests/fixtures/durable/harness-round-v1.json \
  "$scratch/reference/harness-round-v1/trace.json"
```

The standalone codec regression uses the pinned Node v26.12.0 and imports the
actual MTS encoder. It verifies dense undefined/null/literal-marker distinctions
and rejects sparse arrays. Ordinary pytest does not launch Node or require a
host Node version. DE01b3's mandatory reference CI must explicitly run this
codec command alongside fresh acceptance. This mandatory hosted job remains
DE01b3b; a local pass does not establish pushed-job evidence.

`reference` always runs all six registered case families, serially in fresh child
processes and fresh storage/faux state, following one locked installation.
`--output` must be a new owner-only directory outside upstream; its parent must
exist. The upstream argument must name a checkout root, not a package directory
or bare repository. The runner uses read-only `git archive` of the exact commit
and original `package-lock.json`, then `npm ci --ignore-scripts --no-audit
--no-fund` inside the private snapshot. It never uses the upstream working tree,
node_modules or build products and never installs, checks out or builds there.
Every archived file is hashed before installation and verified unchanged after
installation and each case. Complete evidence is retained until explicitly
removed by its owner. Each command has a deadline (900 seconds installation,
60 seconds per case, 120 seconds otherwise); timeout/interruption kills and
reaps the child process group.

For `reference`, exit **0** requires all six complete real case families to match; **1** means complete,
valid evidence has a semantic mismatch; **2** means invalid/unavailable evidence
or a failed prerequisite/child. No missing case, runtime, dependency, source,
input, fixture or module can become a successful skip. Standalone `compare`
compares supplied traces and does not independently attest their provenance.
Malformed lock objects and resolution records are invalid evidence, not valid
mismatches. Existing ordinary Python CI does not run the durable reference.

## Closed manifest and source provenance

Manifest integer version **3** has shared reference/runtime identities, exact
`adapters` and `comparison` hash maps, exactly six unique `cases`, and OPEN
Python/parity status. Each case pins input/expected paths and SHA-256, trace and
projection versions, entrypoint, required loaded source anchors, and exact
source/test identity and hash. Unknown fields, versions, cases, profiles,
destinations, duplicates, aliases, unsafe paths, missing files and drift reject.
Serializable inputs contain only explicitly scripted synthetic values.

The driver preloads pinned
`packages/coding-agent/src/experimental/source-resolver.ts` with `node --import`.
Its module hook refuses nonlocal modules and paths outside the snapshot. Every
recorded file belongs to exactly one checked provenance class:

- Original archived source, with original inventory membership and exact hash.
  Workspace `@earendil-works/*` imports must resolve to `packages/` source and
  may contain **no** `node_modules` component.
- An exact caller adapter artifact from the eight-file manifest map: driver,
  tagged-value contract, the five trace case modules and the storage workload. Copied hashes are checked
  after each case; archive collisions refuse. This is no directory exception:
  undeclared helpers or newly invented `packages/` files reject.
- A physically installed locked registry dependency, with matching package
  version, resolved URL, integrity/link evidence and module hash unchanged from
  the post-install dependency inventory. npm checks archive integrity during
  installation. No global or upstream node_modules can supply a missing module.

The driver starts before its own hook; its exact copied hash is attested
separately. The preloaded source resolver is also explicitly source-hashed.
All three Python verification artifacts are manifest-hashed before and after the run.
The separate closed verification inventory also hashes all four Python test modules
and the permanent Node codec test. Manifest3 separately binds the one workload
input/name/module contract, four oracle identities and coverage index; no timing
result is a committed expected fixture.
The exact manifest byte hash is captured before validation and must remain the
same after execution; updating a golden and its manifest hash mid-run cannot
attest a different configuration from the one actually compared.
Node builtins are runtime-internal, identified by the exact Node runtime pin;
they are not claimed to be archived workspace files.

Four precisely named, already-extraneous records in the pinned lock are copied
by npm into its hidden lock but not installed. They are recorded separately as
`absent_extraneous_lock_records`, never as present or usable dependencies. This
is no general missing-dependency allowance: an actual resolution through any
such record fails.

## Trace versions and retained observations

`kernel-memory-v1.json` remains byte-for-byte the DE01a fixture. Its envelope
has `schema_version: 1`, `case_id`, `normalization: "finite-json-v1"` and ordered
`steps`, each with `op`/`value`. Its ten steps retain absent read, conversation
creation, full commit publications/Chord operations, document commit/rollback
error, detached earlier/current history, storage read and close. IDs and commit
sequences stay actual. Its reserved `{"$undefined": true}` marker remains a v1
representation; Chord wire encoding is reference data, not Python product API.

New cases use integer trace version **2**, the same envelope plus
`codec: "tagged-js-v1"`, and one registered observation step:

| Case / profile | Actual observations |
| --- | --- |
| `harness-round-v1` / `harness-identity-v1` | Settled submission; complete oldest-first history and terminal task records; actual invocation args/count; two full provider request message lists with selected session/cache options; ProviderDoc, UsageDoc and session usage, LiveDoc, reports, faux counters and close |
| `context-heads-v1` / `context-identity-v1` | Complete raw history, first reset-headed and final summary-headed ContextViews (head, entries, contributions, messages), requested final cutoff and close |
| `tool-recovery-v1` / `recovery-identity-v1` | Four stored/current safety variants; actual SQLite close/reopen; admission, before-close/reopened task/history/provider/live facts, final tasks/history/usage/requests, correlated execute facts/counts, fixture timer facts and both closes |
| `durable-abort-v1` / `abort-identity-v1` | Actual task admission/running/mark publications with commit sequences, cancellation continuation, late memo/commit outcomes and errors, callback-entry flag, fresh abort invocation/current task, final task/history and close |
| `env-conformance-v1` / `env-path-time-v1` | Two exact exported cases against actual NodeExecutionEnv; reader operation/Result/bytes/FileInfo/Error DTOs, literal argv, settled stdout/stderr, exists result and cleanup |

H1 deliberately adapts two upstream tests: the echo tool round and explicit
null-details precedence. It acknowledges `details({old:1})`, then returns echo
output with own `details: null`. API, tool call ID and semantic timestamps are
scripted; faux prompt/cache usage is measured, including all nonzero counters
and costs. C1 runs the source scenario's self reset, assistant, summary pointing
at that assistant, then tail. Marker identity and head target remain distinct;
selected order `[summary, after, tail]` is not numeric-ID order. No Vitest suite,
private user data or real provider transcript is imported.

New observation values encode **every** ordinary object as
`{"type":"object","entries":[[key, encodedValue], ...]}`, every array as
`{"type":"array","items":[...]}`, and own undefined as
`{"type":"undefined"}`. Null and finite scalar JSON stay scalar. Thus a literal
payload object shaped like a codec marker cannot impersonate undefined.
Absent keys, own undefined and null differ. Sparse arrays are rejected, including
nested holes and inherited array indices; a missing own element cannot become
null or own undefined during serialization. Duplicate object entries/JSON keys,
invalid tags, nonfinite values, invalid Unicode scalar strings and unsafe
integers (including integral floats/e-notation) reject. Object-key order alone
is immaterial; true array order, boolean versus number and all payload fields
remain significant. Integral finite `1.0` and `1` share numeric representation.

## Bounded projection with raw evidence

Expected fixtures are actual **raw** synthetic reference observations, not
handwritten output predictions. Python is the single projection owner for both
expected and actual. It returns a projected trace plus a pointer witness; the
raw trace is never mutated. Witness paths use JSON Pointer relative to the
decoded observation, with rule, original and projected values. Recompute these
from raw traces; supplied projection/witness files alone are not attestation.

Projection is limited to these typed fields:

- One global durable ID domain across entries, tasks and submission; root
  conversation stays reserved `1`. Entry anchors derive from full history's
  kinds/message roles and causal occurrence, tool-call versus answer; tasks
  anchor to their terminal entry, submissions to the input. Entry
  `id/conversationId/head/byTaskId/edits[].target`, submission
  `id/conversationId/entry/answer`, task `id/conversationId/owner`, model-tool
  input `assistant`, terminal result `entryId`, C1 requested cutoff and copies
  in ContextView head/entries use the same reversible mapping. Unknown or
  colliding identities reject. Selected context never invents its own anchors.
- ProviderDoc `sessionId` and the two selected request `sessionId` observations
  share a separate bijection. Reusing or changing an identity preserves that
  correlation; it cannot collapse every request into the same constant.
- Only H1 and H2 `toolResult.durationMs` in history and captured provider messages may
  map finite nonnegative integer milliseconds to an elapsed-ms marker. It is
  measured by upstream `performance.now()`, separately from Harness.now. Copies
  must agree with the same history tool-call origin to project; a discrepant
  copy remains numeric and is detected. Raw values and witness retain actual
  durations. Field absence/type/unit remain significant.

Semantic clocks are explicit, including historical faux timestamps;
there is no timestamp rebasing or global clock/performance patch. Task
started/ended times remain visible. Fixed tool-call IDs, arbitrary data fields
named `id/timestamp/durationMs`, text/whitespace, errors, diagnostics, args,
usage/cache/cost, call counts, histories and causal array order are preserved.
Projected evidence additionally records adjacent numeric ID-order relations
only for actual oldest-first history scans and ascending task scans, including
H2 persistence-phase scans. Upstream spec §1's entry ordering and the storage scan/mintId contract
make these relations meaningful. A bijective old/reset ID swap must differ
rather than disappear. This derives comparison facts without sorting any rows
or imposing a total order across independent record allocations.

The selected acknowledged boundaries do not claim full scheduler/stream or
coalesced progress-publication equivalence. No sorting/drop rule masks events.

Schema checks representation, not golden behavior: an absent tool-result
`details` or coherent earlier context is valid input to comparison. Tests
actually remove the observed own null-details field and substitute C1's prior
context while keeping final raw history/cutoff. Both validate and exit **1**;
malformed evidence exits **2**. H2's duplicate-effect mutant inserts an additional
correlated execute fact after reopen and increments the unsafe effect count;
H3's post-abort mutant makes the late commit fulfilled, sets callback-entered
and appends a correlated forbidden entry. Both remain schema-valid and exit
**1**. Schema does not enforce correct safety counts, refusal or final outcome.
An explicitly present task outcome must be an object; null/list/scalar outcomes
are invalid evidence2, while an absent nonterminal outcome and valid object
outcomes remain comparable.

## H2 persistence and controlled fixture clock

Each safety variant owns one real Node SQLite file, models/faux/registry and
execute counter across close/reopen. Stored/current policies are unsafe/unsafe,
safe/safe, safe/unsafe and unsafe/safe. The first invocation writes output and
awaits durable details acknowledgement before signaling started and blocking on
its actual abort signal. Cooperative close joins it. Reopen uses the same DB;
task/history/live/provider snapshots are captured **before** explicit resume.
Close itself changes running tasks to pending; this is not a crash/restart claim.
Safe/safe actually executes twice and succeeds; the other three execute once
and retain the interrupted partial output/details. The model continues to done.
No counter, provider cache or response script is reset between connections.

A constant Harness clock proved insufficient for the two progress marks:
`harness/tool.ts` calls `Progress.markAndWait`; `chord/src/output.ts:374–403`
compares the clock against a future throttle deadline. The original reference
chat helper uses Date.now. The bounded fixture adapts `harness-sleep.test.ts`'s
controlled clock/fakeTimers through actual Harness `now`/`setTimer` hooks.
Only an actually requested foreground timer queues an asynchronous callback;
it advances the historical clock to at least the requested rounded-up deadline.
Cancellation suppresses a pending callback. Background timers are explicitly
declined, as in that source helper, rather than advancing cache-expiry time.
Every arm/delay/advance/cancel/callback/background-decline fact is retained raw
and compared; semantic entry/task timestamps remain significant. There is no
global clock patch, sleep, larger timeout, generic scheduler or timer-fact
normalization. This fixture does not claim arbitrary concurrent timer ordering
or product scheduling/performance parity.

H2 uses one durable-ID mapping per independent DB, anchored from final complete
history/tasks/admission and reused across both earlier persistence snapshots.
Typed task checkpoint assistant/tools, waiting-on task IDs, live run inputs/tool
slots and invocation references join that domain. Provider copies remain linked
across reopen. No checkpoint, policy, memo, restart, effect or timer fact is
rewritten to match an expected outcome.

## H3 abort and E1 DTO boundaries

The H3 run deliberately continues after its abort signal. A synchronous host
commit observer captures immutable marked-task publications without Session
calls. It records the asynchronous cancellation continuation, not physical
signal dispatch: Session internal listeners precede host listeners. abortTask
commits and joins the run; a fresh runtime then handles abort. The existing memo
can be read, both late writes reject before the forbidden-entry callback enters,
and the abort handler commits the actual aborted/mark outcome. Only the exact
registered `Task <subject-id> has a durable abort mark` error template receives
the subject's bijective representation; altered text remains significant.
Commit sequences, memo values, event order and all operation statuses remain raw.

E1 selects exported `createEnvConformance` cases by exact unique names, with a
fresh empty private cwd and one actual NodeExecutionEnv callback each. Wrappers
retain real operation results and delegate with correct `this`. The opened
reader is represented by an explicit scoped handle; class internals are not a
portable trace contract. Uint8Array results become byte-number arrays; Error
instances become explicit name/message/own-field/cause DTOs. Own undefined
Result values and Error paths survive. Reader.close and env.cleanup return void.
The source invalid-range and already-aborted calls are actual captured calls.

Only E1 scenario cwd, typed FileInfo.path/FileError.path within that same
controlled root and FileInfo.mtimeMs receive the new versioned projection.
Root-relative filenames and path equality remain visible, traversal/escape
rejects, fractional finite mtime milliseconds stay raw with pointer witnesses.
Arbitrary path/time text and error messages are untouched. Literal argv executes
the source hostile string without shell interpolation; settled ordered text is
captured separately per stdout/stderr. OS chunk partition/interleaving is retained
privately in versioned `raw-chunks.json`, outside this settled-output comparison
surface. That sidecar explicitly contains both selected scenarios, including an
empty reader chunk list; the runner validates text/stream labels and concatenates
each stream against the corresponding actual raw settled observation.
All readers close and the real env cleanup is awaited. This is bounded POSIX
byte-reader/argv evidence, not watch/Windows/full environment conformance.

## Evidence, licensing and remaining gates

Each successful run retains root `run.json`, exact archive, source and
post-install dependency inventories, locked dependency evidence and complete
per-command stdout/stderr/deadline/exit metadata. Each case directory retains
copied input, raw `trace.json`, `resolutions.json`, actual/expected projections
and pointer witnesses, and `result.json` with input/fixture/module hashes.
H2 also retains its closed SQLite files plus `database-evidence.json` with real
paths/sizes/content hashes; E1 retains raw stream chunks. The runner requires
exactly four unique policy DB records with closed=true and exact private paths,
opens only regular nonsymlink files, and verifies recorded size/SHA against
actually read bytes. It explicitly attests the database SHA map in result.json.
Known auxiliary JSON sidecars are validated and hash-bound in each case result. These raw files are
private evidence, not portable golden expectations. Complete success requires
all registered IDs, all four H2 variants and both E1 scenarios.

The fixture manifest pins actual source/test identities. H1 adapts
`packages/durable/test/harness-tools.test.ts`'s named tool-round and
explicit-null-details tests; C1 adapts `harness-context.test.ts`'s newest/self
head test. H2 adapts the unsafe-intent and replay-policy matrix in
`harness-tools-recovery.test.ts`; H3 adapts the durable-mark test in
`harness-tasks.test.ts`; E1 executes the named exported cases in
`src/testing/env-conformance.ts`. Barrier/clock helpers identify their
`task-support.ts`/`harness-sleep.test.ts` origins and hashes. Reference code is MIT,
**Copyright (c) 2025 Mario Zechner**; its complete notice remains in
`tests/fixtures/durable/UPSTREAM-LICENSE.txt`, with attribution in adapted files.
Scenario inputs are scripted synthetic data. Expected fixtures retain actual
raw runtime observations, including controlled scratch paths, filesystem mtimes
and provider UUIDs. Only the explicitly declared typed fields are projected,
with raw values and pointer witnesses retained; raw observations are not removed
or rewritten. Private command logs stay outside version control.

[Delivery index](specs/2026-10-19-durable-execution-plan.md#5-delivery-order-and-reviewable-slices):
b1 and b2 landed with independent CLEAN and audited pushed CI. Historical ISSUES
and fixes remain in the evidence ledger. b3a adds the local acceptance below;
b3b retains mandatory pinned real CI, selective synthetic artifacts and actual
pushed-job success. b3b landed at `13d061d2` with actual Opus5.5 HIGH CLEAN,
[CI39370449156](https://github.com/ephes/pipy/actions/runs/39370449156)11/11 and
every uploaded artifact entry audited. DE02a1 landed the bounded value/record/port seam below; DE02a2a landed its
internal table comparison and DE02a2b adds bounded current raw-base documents; every original DE01
requirement remains cumulative. No performance, full env/platform, paid-provider
or Python-native parity result is claimed.

## Fresh local acceptance

```sh
scratch="$(mktemp -d /tmp/pipy-durable-acceptance.XXXXXX)"
uv run python scripts/durable_pi_comparison.py acceptance \
  --pi-source /Users/jochen/src/pi-mono --output "$scratch/acceptance"
uv run pytest tests/durable/test_pi_comparison.py \
  tests/durable/test_pi_comparison_cases.py \
  tests/durable/test_pi_comparison_acceptance.py \
  tests/durable/test_pi_comparison_coverage.py -q
node tests/durable/test_reference_codec.mts
```

Acceptance creates an exclusive private root, assigns an execution identity and
bootstraps `reference` exactly once under `reference/`. It accepts no historical
`--reference` bundle or reuse of an existing output. A six-case `reference`0 is
only a six-case result; acceptance0 additionally requires the full H2x4/E1x2
matrix, exported workload checks/readbacks, four fresh valid CLI1 mutants and
complete coverage validation. Prerequisite, malformed, drifted, missing or failed
child evidence returns2; a genuine required reference mismatch returns1. An
oracle's invalid2 or unchanged/no-op0 is a failed acceptance, never a successful
negative. Unit execution doubles/replays are labeled validator/wiring checks,
never fresh reference evidence.

`invocation.json` binds execution ID/root/start and initial manifest identity.
Final `acceptance.json` attests the exact parent reference run SHA/assigned ID,
manifest/coverage/input/verification closure, runtime, workload/hash/module facts,
all four oracles and their full compare command/exit/stdout/stderr artifacts.
Reference archive/source/dependency inventory bytes, copied source/adapters,
physical resolved modules, actual input/raw/projection/witness/result/auxiliary
files and DB byte hashes are rechecked. Start/end manifest and parent byte
identities plus workload/oracle/command artifact hashes must remain unchanged;
revalidating a substituted manifest or internally consistent replacement is not
a freshness proof. Raw evidence is retained. No provenance validator allows a
copied-only old bundle to stand in for the required new bootstrap.

## Exported storage workload baseline

The separate `storage-baseline-v1/workload.json` uses tagged-js-v1 observations,
not the six-case trace schema or a timing golden. It invokes the actual exports
in pinned `src/testing/storage-benchmark.ts`, using actual MemoryStorage and
TIMING_SCALE (1,000 entries,300 tasks,300 family documents). The exported primary
record count is1,870, including auxiliary documents and fork records; it is not
revision/commit/byte count. Twelve unique exported reads use the dataset from the
same live seed and each export's actual expected(dataset). The filtered-task
name contains limit50, while this seed's expected result is5. Entry/address/head
lookups return actual IDs. Replay tails0/16/128/1024, ancient/recent128/256 and
eight-deep fork ancestry are exercised through the exports, not fabricated values.

All12 untimed reads and three fresh untimed write/readback cases pass before any
measurement. Ten measured complete read rounds add120 calls, with each result
checked outside its timed span. Each of the three writers has three measured
samples, each on another fresh seeded/closed MemoryStorage (12 write stores in
all). Real exact entry/task/submission/address/document reads and request-index
lookups verify complete persisted payloads, original baseline preservation,
counts, common commit sequence and mixed record correlations. Return1/100/4 or
an intercepted write list alone is insufficient. Storage closes in finally,
including failure paths; no retained-memory figure is collected.

Raw monotonic start/end spans separate import/process overhead, read seeding,
correctness, each exported run, readbacks and completion. Write timing includes
minting/batch construction and one transparent successful-commit observer append;
seed/readback/assertion costs are outside that run span. The parent separately
records total Node child seconds. This bounded-local-v1 protocol uses no warmups
and differs from both upstream Vitest benchmarks and its ten-total-round memory
study. Hardware reports only OS/release/arch, CPU model/count, total RAM and exact
Node/npm/Python versions, without hostname, username or environment dumps.
Measured durations are finite/nonnegative raw contextual evidence, with no
throughput threshold, timing equality, performance parity or retained-heap claim.

## Exhaustive current and future evidence index

Version2 `tests/fixtures/durable/coverage.json` contains107 explicit rows:
DE00–DE45, P01–P43, DE01a/b1/b2/b3/b3a/b3b, DE02a/a1/a2/a2a/a2b/b,
DE02b1a/b1b/b2/b3 and DE02b2a/b2b. All cumulative
parents remain visible, including the original DE02 acceptance unchanged.
Each row retains original canonical plan text, owner slices and future acceptance.
P source anchors match the exact corresponding plan source cell; DE source anchors
bind the actual slice contract line (or cumulative child range). Bounded current
case/workload/oracle IDs and appropriate existing evidence links are explicit.
Status must agree with the ledger, with cumulative parent status derived from
its children. b3b has empty reference-family own current evidence; its
inherited_reference field separately attributes existing LOCAL b1/b2/b3a facts,
with actual b3b delivery recorded separately from inherited scenario ownership.
Validators reject omitted,
duplicate or unknown IDs, changed ownership/future/source/status contracts and
dangling or unrelated DE evidence links. Malformed plan rows and absent mutation
preconditions fail as explicit invalid evidence2, with no lookup traceback.
Source-only, reference-only, delivery and the narrowly owned DE02a1/a2a/a2b/b1b
bounded-native value/record, internal-table and guard evidence are distinguished; no full native-parity evidence is claimed. Unexecuted future cases cannot enter the executed
registry. Every P row, including P43, remains OPEN. Reference evidence is partial;
DE02–DE45 still owe their concrete Python behavior, native tests, crash/race/wire
and integration acceptance from the unchanged parent plan. DE02a2a owns internal tables/index/Seq/race comparisons; DE02a2b retains raw-base
in the same candidate and cumulative bounded Storage proof. Both have landed, so the
cumulative a2 and a parents are delivered. DE02b is split b1a→b1b→b2→b3: the
landed b1a private guard/lifecycle owner has native tests only (source-only index
evidence); b1b alone owns the bounded-native `session-guard-b1b-v1` case, its
actual pinned Node guard oracle; both have landed. b2 is split b2a→b2b: the
landed b2a private Tx core and raw-base drafts and the landed b2b first internal
Session (line, sync and async commit, publication, close) have native tests only
(source-only index evidence), and the landed b3 alone owns the bounded-native
`session-commit-b3-v1` case, so DE02b and DE02 are delivered. DE03 is split
a→b→c→d before code; DE03a's ownership and connection tests are native only
(source-only index evidence). DE03c1 landed `1563bb68` with table contract
cases, and DE03c2 (landed `a2c4c6d`) extends these native-only tests to raw-base
documents across
Memory/SQLite/reopen, lossless signed zero and numeric versions (D2 (b)/(e)),
retention rows, real write refusals, acknowledgement loss and SIGKILL cuts.
DE03c2 landed `a2c4c6d2`. DE03d is split: d1 adds native Session/SQLite and
bounded workload evidence; d2 retains the actual paired Pi SQLite probes of
D2(a)–(f), two fresh reference chains and the final DE03 gate. No paired SQLite
execution is claimed by d1. No
child alone can complete DE03 or any P row.


## Mandatory reference CI and selective artifacts

The `Durable pinned reference acceptance` job runs on every main push, pull
request and manual CI dispatch, alongside the existing ten jobs. It anonymously
fetches only commit `2ec55d808d455b1a64a2bf7888198207a096120a` from
`https://github.com/earendil-works/pi.git` into a private new Git repository and
checks tree `b51be844ffaf57e96d06a58d3f45ee90c6ba7401`. The official Linux x64
Node26.12.0 archive is checked against SHA256
`fd47865ecd6391ebb431fc815e01b9135bf83ded5cf373247e9331e196b7aded`
and the versioned official SHASUMS file; bundled npm must be11.20.0. No upstream
working tree, credentials, provider or mutable dependency cache participates.
The uploader action's separate Node24 runtime does not select the reference Node.

The private root is declared at the Acquire and Collector step `env` boundaries;
the uploader uses the same controlled path in step `with`. The `runner` context
is deliberately absent from job-level `env`, where Actions does not provide it.
In the job's minimal child environment it runs the permanent codec test and
exactly one fresh acceptance, using the existing CLI:

```sh
node tests/durable/test_reference_codec.mts
python scripts/durable_pi_comparison.py acceptance \
  --pi-source "$DURABLE_CI_ROOT/upstream" --output "$DURABLE_CI_ROOT/acceptance"
```

The job retains full bounded acquisition, runtime, codec and acceptance command
logs. Inner case/workload/oracle/npm deadlines stay unchanged; the outer
acceptance process has a1800-second operational ceiling, with GNU timeout sending
INT followed by KILL after30seconds; the wrapper waits up to1840seconds. Existing
runner process group termination/reaping handles its inner deadlines and
KeyboardInterrupt. The eleven child ceilings total3180seconds; the Acquire
step allows3300seconds including120seconds wrapper overhead. Checkout/setup/
collector/upload each allow300seconds, artifact identity60seconds: all step
ceilings total4560seconds. The5100-second job cap retains540seconds of margin;
job cancellation cannot be assumed to provide the controlled inner deadline.
Host termination or arbitrary
SIGKILL cannot provide a universal no-orphan guarantee. Every required failure
remains a failed job, including acquisition before acceptance creates its root.
No missing prerequisite becomes a skip or a green upload.

The new collector stages only exact registered textual roles:

```sh
python scripts/durable_reference_artifacts.py \
  --acceptance "$DURABLE_CI_ROOT/acceptance" \
  --job-evidence "$DURABLE_CI_ROOT/evidence" --output "$DURABLE_CI_ROOT/staged" \
  --pipy-commit "$GITHUB_SHA" --repository "$GITHUB_REPOSITORY" \
  --run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" --job-key "$GITHUB_JOB"
```

Output must be new and outside its inputs. Exit0 means verified textual staging
of the complete original evidence; exit2 means invalid or partial evidence, not
a semantic mismatch. Local mode is explicitly labelled local and cannot attest
a GitHub upload. The wrapper writes schema1 `job.json` containing origin, exact
workflow identity, completed command order, completion and the observed Node
archive size/hash. Each named command has existing runner JSON/stdout/stderr
metadata. Hosted order is source-init/fetch/ref/tree, node-download/shasums/
extract/version, npm-version, codec, acceptance. Local validation records only
actual codec/acceptance commands and no hosted download attestation.

Schema1 `index.json` binds commit/repository/run/attempt/job key, acceptance and
parent execution/manifest identities and every selected source/staged byte size
and SHA256. `GITHUB_JOB` is a key; delivery additionally binds the numeric API
job ID. The index has no self-hash; its uploaded bytes are hashed externally.
Expected golden projection/witness files are independently recomputed before
staging. All actual raw traces, witnesses, versioned E1 chunks, hardware/workload
readbacks, four oracle pairs, H2 sidecar and result DB attestations remain intact.
No ID/time/duration rules are added and no raw bytes are redacted to obtain a pass.

Only exact named text files are eligible, with a32MiB file and256MiB aggregate
limit, strict UTF-8 and no NUL. Reads reject traversal, aliases, symlink leaves or
parents, hardlink aliases, nonregular files, changed identities and changed bytes
before copying. Each directory is pinned with dir_fd/O_NOFOLLOW during opening,
so replacing an intermediate directory cannot redirect a read.
Unknown files are never selected. Invalid evidence preserves only independently
safe registered wrapper logs and a failed/partial index, including missing roles.
Its `missing` roles are fully examined even when another role is unsafe;
`invalid` records unreadable/unsafe role classes without copying their bytes.
Malformed nested helper inventories also produce exit2 and a failed index, with
only the exception class recorded. It never requires an acceptance manifest to
report an earlier failure.

SQLite bytes, `archive.stdout`, `reference.tar` and the Node archive are explicitly
`uploaded:false` with observed size/hash attestations; no DB binary is uploaded.
Snapshot, node_modules, Git objects, private home/tmp/cache/npmrc, executables and
unrelated files stay local. This is scripted synthetic input and observed raw
runtime evidence: UUIDs, controlled scratch paths and mtime stay raw. Local
operator paths are not hosted upload evidence. Hosted paths come from controlled
RUNNER_TEMP/workspace; delivery audits actual staged bytes for unintended private
content rather than rewriting them. Upstream MIT provenance is unchanged; the
collector and workflow copy no additional upstream implementation.

An always-run upload selects only the staging directory, rejects missing files,
excludes hidden files and retains artifacts for14days. Artifact ID, URL and
digest are recorded in job logs. Upload success cannot clear an earlier failure.
After authorized push, delivery must download the actual uploaded artifact and
verify its digest, index and every included byte/role plus binary exclusions,
exact commit/run/attempt/API job/artifact identities and all six cases/H2x4/E1x2/
12reads/3writes/four valid CLI1 mutants. Local tests, workflow source and a local
stage do not establish this remote proof. Workflow execution is mandatory; server
branch-protection enforcement is a separate read-only delivery observation, with
no remote settings changes in this slice. That delivery was audited at
`13d061d2`/CI39370449156; it is not native product parity.


The local workflow gate separately executes official pinned actionlint1.7.12
against the complete CI file; optional shellcheck/pyflakes integrations are
disabled for this expression/context check. Permanent host-independent tests
cover the known job-context misuse and compute actual inline child/step/job
budgets. They do not substitute for actionlint or a successful hosted job.
Their YAML parser is an explicit PyYAML dev dependency, locked without changes
to any package version/artifact; ordinary Python tests still require no Node.

## Native value and record boundary — DE02a1

`pipy_harness.native.durable.codec` owns already parsed finite JSON values.
`OwnedJson(value)` validates/copies exact plain containers and scalars into a
private immutable token tree; `thaw()` returns an independent plain tree each
time. `encode_value`/`decode_value` validate a closed schema-version1 tree
envelope. These are value/tree APIs, not public JSON text parsers. There is no
arbitrary nesting256 cutoff; finite1500-level copies are tested. Ancestor cycles,
nonplain/coercing containers, nonstring keys and nonfinite values reject without
calling subclass hooks. Repeated acyclic aliases become independent copies.
Equality and hashing of OwnedJson and its record/query/write/document DTO owners
are explicitly representation-exact: float binary64 bits distinguish signed zero,
and Python int versus float, stored object-member order and Python string
spellings remain distinct. This is ordered owned-tree equality, not JSON-semantic
equality or the JS verification transport. Equal owners have equal hashes;
unequal representations remain separate set/dict keys. DE02a2 index lookup still
requires JS UTF-16 identity independently of this representation equality.
All finite binary64 payloads are supported: subnormals, large integral values,
negative zero and lone-surrogate strings/keys survive. Exact Python ints must be
exactly representable as finite binary64; bool remains distinct. IDs/Seq are
separate exact positive safe integers; cursor positions are signed safe integers.
No DE01 TraceSafeNumber/UTF-8-scalar restriction is imposed on product values.
A parsed Python dict containing both an astral key and its literal surrogate-pair
spelling has two members but only one JS UTF-16 property identity. That dict is
unrepresentable and rejects without silently merging/dropping values. Each
individual spelling/string/key is preserved; there is no NFC normalization,
replacement or lone-surrogate ban. This is not duplicate JSON-text rejection.
The Node key-recipe observation truthfully shows one property with last value2,
not Node rejection. DE02a2 must use UTF-16 identity for request/kind/document
indices, independently of raw Python DTO spellings.

`records` supplies owned ConversationRecord, EntryRecord, TaskRecord,
SubmissionRecord, DocumentRecord and DocumentCreate, with version1
`encode_record`/`decode_record` envelopes. All declared fields and variant
presence are retained. `to_tree()` returns detached values; constructors reject
malformed/unknown fields and versions. `OMITTED` is removed only from selected
optional top-level record fields, never from generic payloads or arrays. None is
JSON null. This omission option belongs to constructors only: decode_record first
owns a strict JSON tree, so decoded OMITTED rejects instead of disappearing.
Record admission checks every top-level key's exact string type before
omission membership or copying. Both versioned decoders check envelope keys
before shape/set membership. These boundaries reject subclass keys without
hash, equality or representation hooks; Storage DTOs take ownership before shape
validation. Model-message fields remain full finite JSON here; canonical rich
message codec fields now have the additive DE06a codec; actual task checkpoint
integration remains DE06b and cumulative DE06 acceptance is still incomplete.
Diagnostics/reprs do not print payloads.

```python
from pipy_harness.native.durable.codec import OwnedJson, decode_value, encode_value
from pipy_harness.native.durable.records import EntryRecord, decode_record, encode_record

value = OwnedJson({"large": 2**60, "zero": -0.0, "items": [None]})
assert decode_value(encode_value(value)).thaw()["items"] == [None]
entry = EntryRecord({"id": 2, "conversationId": 1, "kind": "example", "data": None})
assert decode_record(encode_record(entry)).to_tree()["data"] is None
```

`storage` defines the synchronous Storage Protocol and owned query/address,
StorageWrite, Cursor, Page, EntryLookup and StoredDocument DTOs. Its full method
surface retains later document/history/copy roles. A DTO accepting a delta/copy
shape is not backend support. **A1 has no Python MemoryStorage or Session**.
DE02a2 must implement atomic detached Memory, global root1/mint2/seq1 domains,
ancestry/cursors/indices, concurrency barriers and the current raw-base seam;
unsupported commands reject complete batches. DE02b retains all callback,
revocation, cancellation, guard, acknowledgement-loss, adoption/publication and
close semantics. Existing product sessions/storage/defaults are unchanged.

### Bounded actual Pi versus Python comparison

The separate `storage-records-v1.manifest.json` closes native modules, tests,
script, input, caller adapter, pinned source hashes and the unchanged-form
six-family manifest identity. It adds no ninth adapter to that registry and
changes none of the six golden fixtures. Its phase is `values-records-a1`, not
storage conformance. Source-authored synthetic inputs exercise16 distinct values and31
record variants. The actual Node driver executes Chord `copyJson`/`isJsonValue`,
then Node MemoryStorage.commit/exact-reads as a record producer. Python executes
its real ownership codec and record encode/decode against those observed fields.
Node Storage is reference production only; erased TS record types are not runtime
constructors/validators and no Pi-invalid-record refusal is claimed.

Create a NEW complete baseline acceptance for each fresh paired proof:

```sh
scratch="$(mktemp -d /tmp/pipy-durable-values.XXXXXX)"
uv run python scripts/durable_pi_comparison.py acceptance \
  --pi-source /Users/jochen/src/pi-mono --output "$scratch/acceptance"
uv run python scripts/durable_storage_comparison.py run \
  --acceptance "$scratch/acceptance" --output "$scratch/values-records"
```

`run` requires the ORIGINAL complete acceptance/archive/snapshot/closed DBs,
re-attests its source/dependencies/case/workload/mutant hashes before and after,
checks the actual Node/npm pins again, and executes one separately hashed
caller artifact inside that private snapshot. It does not bootstrap by itself
or re-label a historical/copy-only bundle as fresh: `fresh_reference_bootstrap`
is false and the parent execution/hash is explicit. Two independent newly
bootstrapped parents are required for delivery; replay remains replay.

The verification-only scalar transport encodes numbers as exact binary64 bytes,
strings/keys as UTF-16 code units and objects as sorted encoded-key pairs;
arrays, fields, presence and values remain intact. This is neither the product
codec nor Chord wire encoding. UTF-16 transport joins a valid surrogate pair to
the astral Python spelling; it cannot separately test the literal-pair Python
spelling. Those individual Python spelling roundtrips are native unit tests;
the actual Pi observation tests the shared JS code-unit identity. One redundant
transport sample was removed rather than claiming two distinct JS values.
Raw observed/native/roundtrip trees, resolution provenance,
Node-only commit context and actual ownership facts remain available. The alias
negative changes the recorded independence witness, since JSON cannot carry a
live alias graph. Four freshly derived valid field/ID/signed-zero/alias-witness
mutants each require public comparison exit1, with independent positive exit0;
invalid input is2 and cannot count as mismatch. The standalone command is:

```sh
uv run python scripts/durable_storage_comparison.py compare \
  "$scratch/values-records/observed.json" "$scratch/values-records/native.json"
```

The same exit classification applies to `run`. After validating both outputs,
Pi/native or Pi-consumed DTO roundtrip divergence writes result status `mismatch`
and returns1. Its `mismatch-report.json` binds observed/native/roundtrip file
names and SHA256 values, with independent equality flags for both comparisons.
Invalid evidence or failed provenance remains2 and cannot produce a successful
or semantic-mismatch result. A successful run retains `roundtrip.json` too.

These local proofs do not extend hosted CI/collector roles. Ordinary pytest
remains host-Node/network-independent. The existing hosted six-family gate and
permanent `node tests/durable/test_reference_codec.mts` remain unchanged.
Adapted record/storage/driver shapes retain MIT Copyright(c)2025 Mario Zechner;
full notice remains in `tests/fixtures/durable/UPSTREAM-LICENSE.txt`. Exact
reference identities and source hashes live in both closed manifests.


## Internal table comparison (DE02a2a)

The delivered `memory-tables-a2a-v1` phase at `85390e2b` was an internal
**table-only** comparison:17 actual exported Node cases/255 calls, seven paired
recipes/70 operations and seven valid mutants. It explicitly refused documents.
Its actual Opus5.5 HIGH CLEAN and [CI39408774643](https://github.com/ephes/pipy/actions/runs/39408774643)
success11/11 were independently audited. Historical ISSUES, accepted raw-limit/
navigation repairs and the declined performance suggestion remain in the ledger.
Those old private manifests/bundles retain their table-only meaning. The current
CLI advances to the cumulative a2b phase below; it accepts no old-phase shim.

## Current raw-base storage comparison (DE02a2b)

`memory-current-base-a2b-v1` executes real pinned Node MemoryStorage and real
internal Python tables/current raw-base documents, with no product/factory/SDK
integration or full Storage/Session parity claim. Create, base change/version
transition, retire, current materialization, exact-address find and exact-scope
scan all use the **same private candidate/authority and one adoption** as tables.
Delta/copy/numeric historical/fork materialization reject whole requests/batches
and remain DE04/DE27. A2b landed at `72253352`, so a2/a are delivered; Session
DE02b (split b1a→b1b→b2→b3) and DE03 stay gated. All43 P remain OPEN. Original six DE01 fixtures, eight-adapter closure,
hosted CI/collector and permanent Node codec remain unchanged; this local
comparison is separate from that hosted job.

```sh
# Fresh private owner-only root; normal caller HOME, isolated child state.
scratch="$(mktemp -d /tmp/pipy-durable-tables.XXXXXX)"
uv run python scripts/durable_pi_comparison.py acceptance \
  --pi-source /Users/jochen/src/pi-mono --output "$scratch/parent"
uv run python scripts/durable_storage_comparison.py run \
  --acceptance "$scratch/parent" --output "$scratch/a1"
uv run python scripts/durable_memory_comparison.py run \
  --acceptance "$scratch/parent" --output "$scratch/tables"
uv run python scripts/durable_memory_comparison.py compare \
  "$scratch/tables/observed.json" "$scratch/tables/native.json"
```

Each run needs a new output and absent caller destination in the private snapshot.
`run` executes the real driver; it honestly records `fresh_reference_bootstrap:false`
and binds the complete original parent acceptance execution/hash. Two new parent
bootstraps with their own a1/table executions form final local evidence. Copying
or replaying an old bundle is not a fresh execution. Exact archive, original
lock/dependency/source/module hashes and Node26.12.0/npm11.20.0 are verified using
the existing fail-closed bootstrap; no upstream writes/providers/user data occur.
`storage-memory-v1.manifest.json` closes the native implementation, two test
files, caller, input and all a1 helper artifacts (16 paths), plus exact five
pinned source hashes. Hash direction is code/tests/coverage → baseline manifest
→ a1 manifest → table manifest; no manifest hashes itself. Start/end manifest
and original parent byte identities are checked. The child deadline is60s and
process-group cleanup is inherited from the bootstrap; no missing prerequisite
or runtime failure becomes a skip.

Twenty-one **actual exported Node** `createStorageConformance` cases are selected
by their exact source names in the input; each uses fresh real MemoryStorage,
exactly-once callback and finally-close. `reference-context.json` records these
identities/counts; `conformance-raw.json` retains all326 ordered calls, full typed
arguments/results, error names/messages/stacks/causes. These Node assertions
alone are not Python conformance. Eleven **derived paired recipes** separately
exercise both actual implementations: identity/highwater/Seq/empty/exhaustion,
replacement/status/request-index, ancestry/markers/owners, cursors, UTF-16 keys,
live ownership observations, ID exhaustion and current documents/mixed rollback.
They contain128 ordered
operations; the arrays and all outcomes are preserved. Indexed-string recipes
retain their table-only derived labels. The full exported indexed-string case
also runs in Node, but none of the exported Node assertions alone claims Python
suite completeness. Added Node lifecycle/address/rollback cases can use deferred
history/delta APIs; paired Python recipes execute only the declared supported seam. Missing/null/undefined and
all record fields remain distinguishable. Source-order request winners have no
fallback after removal, and legacy cursors without order use the table default,
rejecting an opposite explicit query order.

Raw Memory accepts a dangling parent without Session graph validation. Descending
entry iteration is lazy: a page reads only its `limit+1` lookahead, a scoped point
lookup stops at its first match, and a head lookup returns a local marker before
unneeded ancestor access. Both scan orders stop when the accumulated parent cap
falls below the query minimum. Ascending deliberately builds the capped segment
list before yielding entries. The paired recipe pins these cuts with accepted
raw records; native controls also retain the required parent access when an
ascending scan or page lookahead really needs it. No new graph validation or
exception normalization is introduced.

The dedicated transport preserves finite binary64 bits and UTF-16 code units;
object members are sorted by their encoded UTF-16 keys for cross-language
verification only. IDs/Seq and cursor after positions are typed roles. Negative
integral Number cursor positions are converted to exact Python integers only at
that cursor boundary; payload numbers and signed zero are untouched. Raw backend
labels are retained; comparison projects only `backend` to `storage` and Python
`RuntimeError`/`StorageClosedError` names to Node `Error`. Error messages and
StorageRequestError, call order, IDs, commit sequences, records and cursors are
not normalized. This is separate from representation-exact OwnedJson equality.

`native-context.json` records five independently constructed literal Python
surrogate-pair spellings; UTF-16 transport by itself cannot prove those Python
spellings. Native tests additionally prove actual retained-input/read/page/old-
snapshot isolation and placement independence, with real contended write/write,
write/read, write/mint and write/close barriers on the common authority. The
scripted ownership recipe records its actual commit/read/mutate/reread calls and
facts; serialized facts do not replace these live tests.

For both public `run` and `compare`,0 means bounded equality,1 means a schema-valid
semantic mismatch,2 means invalid evidence/prerequisite. Recorded argument packs
are validated by the same per-operation shape/DTO guard as recipe arguments,
including write/query/ID/cursor fields and zero-argument roles. They are not
required to equal scripted values: valid wrong arguments remain semantic1;
null packs or malformed fields are invalid2. The18-role guard includes all current
methods plus live ownership operations;128 recorded operations cover17 roles.
Exact submission has a direct helper guard, not a fabricated paired CLI claim. A mismatch run emits
`result.json` plus `mismatch-report.json` binding raw and projected hashes; its raw
expected/actual files can be passed back to `compare`. Invalid runs emit no
successful result or semantic mismatch report. Fourteen freshly derived valid
field/ID/Seq/request/status/document-address/scope/cursor/alias mutations require a
separate positive CLI0 and negative CLI1; CLI2 is refused as a mutation pass.
These are local table-oracle mutations, not the four DE01 semantic mutants.

Product code imports no verification tool or fixture. Table/scan code and selected
source-derived cases retain MIT Copyright(c)2025 Mario Zechner; exact sources are
pinned in the new manifest, and the full [upstream notice](https://github.com/ephes/pipy/blob/main/tests/fixtures/durable/UPSTREAM-LICENSE.txt)
is preserved. [The delivery ledger](specs/2026-10-19-durable-execution-evidence.md#de02a2a-local-table-evidence)
records historical table checks and delivery. The [a2b admission](specs/2026-10-19-durable-execution-evidence.md#de02a2a-delivery-and-de02a2b-admission)
and [a2b delivery](specs/2026-10-19-durable-execution-evidence.md#de02a2b-delivery-and-de02b-split)
record the a2b gates and the DE02b split; no CI result is claimed for a2b.


## Observed raw table page-limit boundary

For the exact pinned `MemoryStorage` source (`memory.ts:203–210`, table loops
`447–462`, `547–566`, `575–592`, entry loop `515–538`), the observed integer limits
**0, -1, -2 and +1** have the following boundary in all four table scans:

| Observed limit | Empty table or filtered nonmatching records | Matching records |
| --- | --- | --- |
|0 | Empty page, no next cursor | Raw failure |
|-1 or -2 | Raw failure | Raw failure |
|+1 | Empty page, no next cursor | One full record and the expected ordered cursor when a second record exists |

Actual pinned Node v26.12.0 throws `TypeError` with message
`Cannot read properties of undefined (reading 'id')` for those failure rows.
Current native Python throws `IndexError` with message `tuple index out of range`.
The page builder slices first and then accesses the last item when constructing
`next`; the underlying scans do not impose a positivity check. In particular,
negative-limit empty pages do **not** behave like limit0 empty pages. The permanent
48-case native matrix covers four scans × empty/nonempty/filtered-nomatch × these
four explicit limits, exact full pages/cursors and the raw Python exception.
This empirical domain is not an untested rule for every nonpositive value.

The independent actual Node/Python48-row probe retains both raw exception
classes/messages/stacks and exact nine source-module hashes. It reads an
identified pinned snapshot with new Memory instances and is explicitly not
another bootstrap or full-reference acceptance. No Node dependency is introduced
into ordinary pytest. No error-class equality or full native error parity is
claimed; errors are not projected to force this boundary into the70-operation
paired trace. Existing paired cases and their explicit language projection remain
unchanged. A new positivity guard would change the actual limit0 no-match result,
so this raw backend boundary is deliberately preserved. Session admission and
product integration remain later work; every P row stays OPEN.


## Raw-base grouping and ownership contract

Document actions are grouped per ID against the pre-batch state: at most one
create/base-content command and one retire. Create+retire and live base-change+
retire work in either command order. Duplicate content/retire, unknown/retired IDs
and global ID reuse fail. New empty lifetimes stamp createdAt==retiredAt and keep
the ID consumed. Final live address cardinality is checked only after the entire
private table/document candidate and its owner/status/request/entry/address/scope
indices exist. A supported late address conflict and an allocation fault both
leave the old authority/counters unchanged; unsupported delta/copy preflight is
not used as a substitute for this proof. No fallible document application occurs
after table publication. The deliberately complete candidate has no performance
parity claim; the earlier incremental-index performance suggestion stays declined.

An address distinguishes session/conversation/task scope, kind and singleton
versus explicit family key (including empty key). UTF-16 identity applies to kind
and key, preserving distinct lone surrogates; representation-exact OwnedJson
identity remains separate. Retire-old/create-new at one address works in either
order. A create+retire at an occupied address has net-zero cardinality; conditional
pointer removal preserves the existing current incarnation. `document(id)` never
follows an address replacement; `find_document(address)` does. A base replaces
the entire object/version; retained policy fields remain exact and current
materialization has deltasSinceBase0. Returning them does not implement history.

Document cursors deliberately ignore `order`, resume after the signed-safe ID
ascending and emit ascending next cursors. They do not use table `scanStart`.
Actual pinned Node/native12-row document limit observation (empty/nonempty/
filtered-no-match ×0/-1/-2/+1) retains the same source page-builder cuts described
above:0 without matches empty;0 matching and -1/-2 fail; +1 exact pages/cursors.
Raw Node TypeError and native IndexError remain distinct. This separate probe
uses an identified snapshot and claims neither another bootstrap nor paired
error-class equality. No positivity policy or error normalizer is introduced.

Ordinary host-independent tests cover all Protocol methods/closed refusal, live
input/read/page/old-snapshot/placement ownership, late mixed rollback/allocation,
UTF-16 indices and witnessed mixed write/write, write/read, write/mint, write/close
and failed-writer/waiter settlement on one actual authority. Private guard mutants
remove final cardinality, conditional pointer removal, ascending document cursor,
scope-kind and UTF-16 identity; the permanent tests reject each. Actual exported
Node cases, paired operations, CLI mutants, raw-limit probes and live native
concurrency tests are distinct evidence, never clones or replacements.


## Python point admission boundary

Public document/read/find/scan ingress requires exact built-in types before any
comparison/coercion/hash/repr of caller data. Only exact `current` or positive-safe
integer Seq is a Point; a2b numeric history remains unsupported. Foreign Python
objects and subclasses cannot execute hooks under the Storage authority. Query
DTO ownership rejects foreign values before point validation; close refusal
remains under the same non-reentrant authority. This input-safety boundary does
not restrict raw content.version: raw0/-1/1.5 retain their actual Memory meaning,
separate from later defineDoc's positive-safe definition policy.


## Session guard comparison (DE02b1b)

`session-guard-b1b-v1` compares the **guard and lifecycle owner only**: pinned Pi
`SessionImpl`'s protected guarded Storage against the private native b1a
`SessionLifecycle`/`GuardedStorage`, each over a fresh real Memory backend per
scenario. There is no Session line, Tx, product, factory or full Session parity
claim, and all43 P rows remain OPEN.

```sh
# Same fresh parent as the a1/a2b children above; a new output each time.
uv run python scripts/durable_session_guard_comparison.py run \
  --acceptance "$scratch/parent" --output "$scratch/guard"
uv run python scripts/durable_session_guard_comparison.py compare \
  "$scratch/guard/observed.json" "$scratch/guard/native.json"
```

**Node oracle.** The package entry exports only `createSession`, so the driver
`scripts/durable_reference/cases/session-guard.mts` imports `SessionImpl` from
`packages/durable/src/session/session.ts` in the pinned snapshot. A bounded
subclass adds only a `guarded` getter over the protected `storage` (the
module-private `GuardedStorage`) and records `report`; `beforeClose`,
`assertUsable` and `conversationCreated` stay inherited. `reference-context.json`
records the base class, the exact own members and that `guarded` is a
`GuardedStorage` distinct from the backend. Closing goes only through
`SessionImpl.close`: Pi's `GuardedStorage.close` is not close-once, so the
native facade's close-once `close()` is the owner's role, not a Pi guard claim.
A controlled backend wraps real `MemoryStorage` and scripts per-handle holds and
outcomes (pass, throw, abort-then-throw as in Pi test 410, synchronous throw)
plus a held or throwing backend close (after the real close, as in Pi test 192).
Guarded calls start synchronously, so the backend reads the caller's handle; the
native side uses a thread-local handle.

**Recipes and trace.** `tests/fixtures/durable/session-guard-v1.input.json` is a
closed recipe of27 scenarios/307 steps: all16 delegates pass through; 14 read
variants (13 roles plus the scoped `entry` form) under request error, cancelled
error and cancelled success stay healthy; commit and mint are never exempt
(request error or a cancelled facade); ordinary errors are fatal on mint and on
3 of 13 sampled read roles (b1a's native suite covers all13); Pi
harness-storage-failure tests 121/132/156/192/267/276/291/301/358/410 anchor the
late-read, paged-drain, close-failure, first-cause, invalid-request, reentrant
report and cancelled-read cases; concurrent and cancelled closers, a direct
`fail`, health-before-seal admission, a synchronous throw and a refused commit
proven unapplied by an unguarded backend read complete the set. Both sides emit
one trace shape: per-step outcomes, an ordered effect log (backend call/return,
report with `failure_visible`, close enter/exit) and the terminal end. Errors
project to a closed kind set by strict predicates (scripted identity,
`SessionFailed` with cause, the plain closed `Error`, the closer's abort reason,
real `StorageRequestError`, otherwise unclassified); no class-name map is used.
Caller settlements are recorded per step, never interleaved into effects.

**Bounds.** Pi seals the guard in a line job some microtasks after
`session.close()`; b1a seals inside `close()`. Recipes never admit a guarded call
between a close start and a `quiesce` barrier (Node: one `setImmediate` drain;
native: the closer finished or is blocked behind a held backend call), and the
validator enforces this. That window, with a busy Session line, belongs to DE02b2;
DE02b2b implements it natively (guard reads stay admitted until the line drains)
and proves it in native tests only, not in this oracle.
Also not compared: the order of report versus outcome publication for a healthy
close failure (b1a publishes first; Pi reports first), Python-only
`BaseException` handling, non-Error JS throw values, Harness `safeReport` and
close listeners. b1a's native tests keep the Python-only behavior. Every native
observation follows a join, an Event or a held blocker; threads are named,
non-daemon and reaped, and an unreaped thread is invalid evidence. `run` executes
the native recipes in a bounded child process (120s deadline, minimal
environment) that exits without waiting on a stuck worker and whose process group
is killed and reaped at the deadline, so a never-returning worker still ends `run`
with exit2.

**Exit contract.** `run` and `compare` exit0 for bounded equality,1 for a
schema-valid semantic mismatch and2 for invalid evidence or a failed
prerequisite. Only `run` writes artifacts: hash-bound `result.json` (plus
`mismatch-report.json` on1), and nothing of either on2. `compare` returns only
its exit code; it writes no artifact and attests no provenance. A native `unsettled` outcome or end, or a pending handle outside
the declared blockers, is a valid Python mismatch; in a Node trace it is invalid.
Eighteen schema-valid guard mutants (exemption flips, late success or late error
leaking, first-cause replacement, report twice or missing, close before drain,
double close, admission after seal, swallowed or missing close failure, a waiter
cancel undoing close, seal before health, an unguarded synchronous throw, an
admitted reentrant report, a split cause) each need positive0 and negative1. As
native negative controls, b1a's private source mutants run through `native()`:
eight produce mismatch1, and the three that only b1a's native race and
`BaseException` scenarios reach (split success/error settle, close error
replacing an in-flight exception) stay equal by design.

`session-guard-v1.manifest.json` closes the a2b inventory plus `session.py`, its
test file, this script, the driver, the input and the comparison tests, pins six
reference sources and the two upstream test files, and states the scope above.
Hash direction is code/tests/coverage → baseline → a1 → a2b → b1b. The run copies
the driver to `snapshot/session-guard.mts`, checks Node26.12.0/npm11.20.0 and
module and dependency provenance, and records `fresh_reference_bootstrap: false`,
`session_line`, `tx` and `full_session_parity` all false. Adapted upstream test
support keeps MIT Copyright(c)2025 Mario Zechner and the full
[upstream notice](https://github.com/ephes/pipy/blob/main/tests/fixtures/durable/UPSTREAM-LICENSE.txt).
The [b1b admission](specs/2026-10-19-durable-execution-evidence.md#de02b1a-delivery-and-de02b1b-admission)
and [local evidence](specs/2026-10-19-durable-execution-evidence.md#de02b1b-local-guard-oracle-evidence)
record the gates and the two fresh chains.

## Session commit comparison (DE02b3)

`session-commit-b3-v1` is the cumulative paired acceptance for the first internal
Session: the actual pinned Pi `SessionImpl` against the private native
`session_core.Session` in **both** native modes, sync `commit()` on owned threads
and async `acommit()` on one event loop, each over a fresh real Memory backend
per scenario. It closes the DE02b gate together with the
[guard comparison](#session-guard-comparison-de02b1b) (b1b) and the a1/a2
storage comparisons above; there is no product, factory, typed-document, fork or
full Session parity claim, and all43 P rows remain OPEN.

```sh
# Same fresh parent as the a1/a2b/b1b children above; a new output each time.
uv run python scripts/durable_session_commit_comparison.py run \
  --acceptance "$scratch/parent" --output "$scratch/commit"
uv run python scripts/durable_session_commit_comparison.py compare \
  "$scratch/commit/observed.json" "$scratch/commit/native-sync.json"
```

**Node oracle.** `scripts/durable_reference/cases/session-commit.mts` uses the
same bounded `SessionImpl` subclass as b1b (a `guarded` getter for off-line
guard reads and a recording `report`; nothing else is overridden) and closes only
through `SessionImpl.close`. Two oracle adaptations are recorded in the manifest
scope: raw-base documents are `defineDoc` tokens with `checkpointWhen: () =>
true`, so every loaded change is written as a base like the native raw-base
writes, and `createTask` receives a structural task-definition stub. The clock
is a constant and the document timer is declined, so nothing is swept or
evicted. `reference-context.json` records the own members, zero sweeps and the
number of admitted `document.change` writes, all of them bases.

**Recipes and trace.** `tests/fixtures/durable/session-commit-v1.input.json`
holds 41 closed scenarios (405 steps; 35 also run in sync mode): commit and
tables (minted IDs, before/after-acknowledgement cuts, a request error from
commit, IDs never reused, ingress with no alias, read-after-write, final owners),
raw-base documents (create/change/adopt identities, copy per placement, the
empty batch, escaped/returned drafts, callback failure, pending acquisitions, no
late initializer, a captured Tx, memoized duplicates, the old revision during a
held commit, assembly rollback, retire and recreate, a cold load), line and
cancel-view cuts, failure poisoning and late-read discard, publication order and
listener containment, and close drain windows. Callbacks are closed programs
interpreted identically on both sides. Each scenario yields per-step outcomes,
one ordered effect log (backend call and return with arguments and values,
publication with its changes, close listeners, reports, initializers, marks,
caught errors, close enter/exit) and the terminal end. Errors project to a closed
kind set (scripted identity, `SessionFailed` with its cause, the commit's abort
reason, `ReadAfterWrite` with its method, revoked drafts, settled Tx, pending
operations, closed Session, request errors, and plain core errors with
byte-equal messages); anything else is `unclassified` with its producing side and
never compares equal. Publication change order is compared strictly.

**Cell map.** The recipe's closed `cells` map splits every original DE02,
DE02b and retained DE02b2 cell into 22 atoms and names, for each, the paired
scenarios and the native tests (resolved by `ast` against the b1a, b1b, b2a and
b2b suites) that carry it. Adoption failure and reentry are native-only (the
bounded subclass cannot reach the host-built `Transaction.adopt`; Pi hangs on a
line reentry), and the "Kernel/storage spec" atom is this section with the
[harness spec](harness-spec.md).

**Bounds.** Every concurrent fork is joined by a barrier before the next
effect-producing step; after an idle close no guard read runs before a `quiesce`,
while a busy line keeps guard reads admitted until it drains (Pi seals its guard
in the close line job). Native-only, never paired: adoption failure, reentry,
every `Task.cancel` row, Tx/draft affinity, coroutine listeners, bulk
queued-rejection timing, background-close reporter routing, process-control
exceptions, a sync callback returning an awaitable, and a sync commit settling
while its own inline backend close is held (without a loop the failing commit's
thread runs the claimed close before it returns; recipes settle it after the held
close is released). Not exercised: a caller mutating an entry draft's top-level
keys while the append is pending (Pi reads them after its awaits, native at the
call; a nested mutation is captured on both sides and is paired). Not compared
at all: typed documents, migrations, families, task-scope documents and
terminal-task retirement, deltas and op-based no-ops, forks, `documentState`,
`watchDoc`, retention, `unloadDocuments`, telemetry and listener context, Harness
hooks and SQLite.

**Exit contract and controls.** As for b1b: `run` and `compare` exit0 for equality
(Node against both native modes), 1 for a schema-valid mismatch and2 for invalid
evidence or a failed prerequisite (`compare` of two native traces of different
modes is invalid); only `run` writes `result.json` (plus `mismatch-report.json`
on1). The native recipes run in a bounded child with a 240s deadline. Twenty-four
schema-valid trace mutants (queued cancel running the callback, cancellation
rolling back a running or admitted commit, FIFO reorder, read-after-write
allowed, unchecked final owners, reused IDs, a late initializer, a live escaped
draft, pending operations committed, the callback error lost, a caught read
committing, a late read leaking, before-ack applied, after-ack adopted,
publication before the commit returns or on failure, internal listener errors
reported only or rejecting the commit, host listeners skipping the rest, close
before admitted work, admission after close, close listeners after the report,
the guard sealed early) each need, in every `run` and whatever the native
outcome, positive0 (the observed trace against itself) and negative1 (against the
mutant), as in a1/a2b/b1b, so a valid Node/native mismatch still exits1 with
`result.json` and `mismatch-report.json`; the permanent tests also compare each
mutant with every native mode it runs in. Rerun through the native runner, b2b's nineteen source mutants flip the
designed cells (the line hold during drain and the pending check in async mode;
the queued-cancel cut, post-callback health check, internal-versus-host routing,
close predicate and close-listener order in both), leave the Python-only ones
equal and fail closed (exit2) for a close run on the event loop.

`session-commit-v1.manifest.json` closes the b1b inventory plus `tx.py`,
`session_core.py`, their native test files, this script, the driver, the input
and the comparison tests; it pins twelve reference sources and four upstream test
files and states the scope above. Hash direction is code/tests/coverage →
baseline → a1 → a2b → b1b → b3. The [b3 admission](specs/2026-10-19-durable-execution-evidence.md#de02b2b-delivery-and-de02b3-admission)
and [local evidence](specs/2026-10-19-durable-execution-evidence.md#de02b3-local-session-comparison-evidence)
record the gates and the two fresh chains.

## Additive canonical DTO codec (DE06a)

The private [canonical DTO codec](durable-canonical-codec.md) encodes canonical
messages, frozen requests and provider/tool outcomes without runtime hookup.
DE06a depends on DE02a1 and the existing DTOs; DE06b depends on DE05 and DE06a
and owns actual checkpoint/phase integration. The original DE06 acceptance
remains cumulative. [The backlog](backlog.md#additive-canonical-dto-codec-de06a)
and [evidence ledger](specs/2026-10-19-durable-execution-evidence.md#next-delivery-gate)
own current status and delivery gates.


## Native current-base SQLite workload (DE03d1)

`scripts/durable_sqlite_workload.py run --output NEW_ROOT` creates a new private
artifact root and refuses any existing path. `verify ROOT` validates the closed
input, script/runtime-source identities, artifact hashes, correctness and
budgets. Exit 0 means complete verified native execution, 1 means a well-formed
behavior/budget mismatch, and 2 means invalid/incomplete/drifted evidence. This
is native evidence only; DE03d2 owns comparison against Pi.

The closed input `tests/fixtures/durable/storage-sqlite-workload-v1.input.json`
selects upstream timing scale: 1,000 entries, 300 tasks and 300 current-base
family documents, batched by 100. Three read rounds cover exact entry, first
100 entries and cursor continuation, the five matching filtered tasks, exact
family address and current document contents. All seeded records are read back
exactly, including entry commit sequences and request/document indexes. Three
samples of each upstream write shape (one 128-byte-text entry, 100 entries,
and a mixed entry/task/submission/base-document commit) run cumulatively on
that timing-scale dataset; this deliberately does not reproduce the upstream
write benchmark's separate 100-entry baseline. Each sample records before/after
public-scan counts, IDs, submitted count, actual returned commit sequence and
timing; every resulting record is read back after clean close/reopen. Native
Storage returns a sequence, not a record count. All workload sizes and rounds
come from the loaded closed configuration; a smaller private test configuration
checks that the algorithms consume those values.

One bounded child performs the clean workload; another seeds and announces a
committed WAL before the parent sends real SIGKILL; a third bounded child
recovers and checks the unclean store, including all persisted records and
`integrity_check`. These private child targets never spawn descendants. On
startup/readiness failures and deadlines the parent kills the group while its
leader remains unreaped, then reaps the leader and closes all pipes. Normal
completion performs no later signal or existence probe on a reusable PGID. Recovery/open timing includes unclean-open
validation/quick_check and is reported separately from read timing. Hardware,
Python/SQLite versions, backend/durability, runtime import path, PRAGMAs and
DB/WAL byte sizes remain in the artifacts; WAL is measured before clean close
and after final close, rather than assumed absent. Database access never opens a second
same-process descriptor while a pipy owner holds the inode.

Initial macOS observations were approximately 0.15–0.17 seconds for seeding,
0.02–0.03 seconds for the selected reads and 0.001/0.007 seconds for clean/
unclean reopen, with databases below 1 MiB. The practical absolute ceilings
are deliberately generous: 30 seconds each for seed/reads, 15 seconds for each
open/write sample, 16 MiB database and 32 MiB WAL, with a 120-second watchdog
per native child. They are behavior/performance guards, not timing goldens or a
reference/native speed ratio. Linux measurements and independent review remain
delivery gates. Delta tails, as-of, copies, historical reconstruction and deep
fork seeding are explicitly excluded; DE27 retains the relevant capabilities.
All P rows, including campaign-wide P43, remain OPEN.


## Closed SQLite oracle and lifetime witness (DE03d2)

`scripts/durable_sqlite_comparison.py run --acceptance BASELINE --output NEW`
runs a copied, pinned Node driver against Pi Memory and SQLite. Its SQLite-reopen
leg is a driver-local reconstruction of pinned Pi test `ReopeningStorage`, with
call-time delegation and reopen in `finally` after every commit, including failed
and nested helper commits; it does not import or execute that test helper. It executes the
existing11 scripted scenarios/128 operations and23 valid D2(a)–(f) scenarios/
130 operations on each backend, plus21 exported conformance cases per Pi
backend. Native SQLite and reopen execute the same34/258 scripted inventory;
the exported TypeScript conformance is reported separately, not claimed as
native TypeScript execution. Each scenario owns a separate private store.

The comparison retains raw tagged binary64/UTF16 values and exact error name,
message, code and diagnostic stack.64 closed backend/recipe/step declarations
map only known language/backend error spellings. Pi declarations must match the
exact current raw Pi error; canonical drift or projected Pi EXPECTED is invalid
evidence.
Native entries are a conditional spelling dictionary and may go unused; an
unexpected native error remains a mismatch.126 exact divergence pointers bind current Pi Memory and SQLite
observations, with no missing, stale or unused declaration admitted. Native
values must retain Memory semantics at these points, including signed zero,
raw numeric base versions and atomic rollback differences. Negative integral
page sizes receive an integer conversion only at that API role, just like signed
cursor positions; payload numbers and raw document versions remain untouched.
Valid document scans supply both scope and `at:'current'`.

One separately closed lifetime witness retains the existing unused-ID contract:
`document-atomic/highwater-retained` returns7 in Memory/live SQLite and6 after
SQLite reopen, in both Pi and native. The preceding unused mint was not stored
by the failed commit. This agrees with the documented internal SQLite contract;
it is neither a production fix nor a broad semantic ignore. Forcing native
reopen to7 must fail comparison even though it would match live Memory.

`compare EXPECTED ACTUAL` requires raw Pi reference EXPECTED. Native ACTUAL may
contain raw or already-projected spelling: the conditional native dictionary is
idempotent, so both forms produce the same verdict. Projected files remain
hash-bound derived reports; Pi projected EXPECTED is rejected. Compare returns0 for valid paired equality,1 for a valid native
behavior mismatch and2 for invalid evidence. `run` preserves mismatch1 and its
raw/projected hash-bound report before attempting any positive controls.21 valid
behavior controls include all14 a2b controls plus reopen loss, next-ID/sequence,
signed-zero/raw-version loss, wrong error semantics and the reopen-lifetime
case. If an actual native page omits its continuation, the dependent operation
records a closed `blocked` outcome with its original cursor reference. No cursor
or Storage call is invented; the raw trace compares as mismatch1 and retains the
hash-bound mismatch report. Invalid metadata/declarations remain refusal2.

The SQLite manifest is last after the existing five-manifest cascade and binds
all native SQLite/D1/D2 source/tests and exact parent hashes. Portable hashes of
D1's delivered native workload evidence remain explicitly native evidence,
including its honest known-preview-flake exception. Fresh gate runs attest a
current baseline parent before and after execution, source/resolver/dependency
bytes, runtime versions and copied-driver identity. Two fresh complete chains
are still required for delivery. Delta/copy/history/as-of capabilities remain
DE27, product defaults do not change, and all43 P rows remain OPEN.

## Additive streaming line scanner (DE32a)

[DE32a](durable-line-scan.md) is the pure bounded scanner prerequisite, dependent only on DE00. It owns exact raw offsets and replacement-decoded sizes without retaining file contents. DE32b depends on DE31 + DE32a and retains all filesystem/reader/list/watch/shell/cancellation/spill/cleanup acceptance, including scanner wiring. DE32 remains IN_PROGRESS until both children land; DE33 still waits for full DE32 and every P row remains OPEN. DE32a landed at `52ecc1fc`; see the [delivery evidence](specs/2026-10-19-durable-execution-evidence.md#de32a-delivery-and-de04a-repaired-main-integration) for completed gates.



## Typed current documents (DE04a)

The private `documents.py` defines generic immutable `Doc` and `DocFamily` tokens
through `define_doc`/`define_doc_family`. `doc`, `retire_doc`, `snapshot` and
`asnapshot` resolve the same UTF-16 kind/key identity with explicit owner/key
keywords. Singleton keys/seeds and non-exact owners/keys/policies are refused by
the Python contract; those stricter ingress checks are not invented TypeScript
runtime overload errors. Empty family keys differ from singleton absence.
Only acquisition receives a creation seed. A same-Tx address memo reuses the
first handle/seed before duplicate seed/version admission; fresh stored access
still copies/validates its first seed without rerunning initialization.

Pinned call order matters: staged terminal candidates reject before memo lookup;
loaded existing documents bypass committed missing/terminal task guards, while
absent acquisitions reread the latest candidate after load. Retirement does not
acquire or initialize and does not require an open task or matching content
version. Final terminal task candidates retire staged members and all persisted
unknown kinds/family keys via256-item pages, deduplicated by ID. Newly created
terminal tasks skip the committed scan. Task publication ownership is resolved
from the latest task before backend admission, never from an unrelated newly
created conversation. Cache entries remain committed-only; old snapshots stay
immutable, retirement evicts each key and new retired incarnations never enter
cache. The original `RawDoc` precursor is removed outright.

`durable_documents_comparison.py run --acceptance BASELINE --sqlite D2_OUTPUT
--output NEW` consumes the actual attested D2 result plus baseline. The copied
pinned Node driver executes24 recipes A1–A9/T1–T10/P1–P3/C1–C2 on real Pi Session
and Memory. The native paired legs execute all24 with real Memory, SQLite and
SQLite-reopen in async mode. The separate native Session matrix exercises sync
and async; eighteen cases explicitly skip only same-Tx pending/held async
interleavings or unawaited operations that the blocking sync facade cannot
express. It does not invent cross-thread SyncTx authority. Full raw calls,
errors/code/stacks and Pi publications remain external hash-bound artifacts;
the lossless bounded compressed fixture is captured unit data, not a fresh gate.
Eleven exact recipe/error spelling pairs preserve unexpected native errors as
mismatch1 and stale Pi declarations as invalid2. The Pi EXPECTED side must be
raw; native conditional spelling projection is idempotent. Snapshot absence and
omitted scan cursors use explicit role-local null observations, with raw Node
undefined retained in the raw trace. No payload/version numeric normalization
is introduced.

Pi loaded changes select full bases using the existing reference-only
`checkpointWhen:()=>true` adaptation. Native remains full-base/value-based in a;
operation batches, structural no-ops, actual delta materialization and real
checkpoint-policy exceptions remain retained DE04b acceptance. Raw Pi ops are
retained but operation parity is not claimed. Full history/as-of/fork/migrations
remain DE27; no Storage or product-default capability changes. D1 historical
proof/source hashes identify their immutable delivered snapshot, independently
from the current source closure and migrated D1 Session tests. All43 P rows
remain OPEN.

The typed-document CLI binds all38 actually loaded upstream source modules to
its closed pinned manifest, and every loaded npm dependency file to the parent
`dependency-sha256.json` inventory. Unbound source imports or changed dependency
bytes are invalid2. Child HOME, temporary files, npm cache and empty npm config
files belong to the new output via the established `child_env` helper; native
and compare children also receive the output-local HOME. The only intentional
new predecessor entry is the phase's copied driver in its private snapshot.

DE04a landed at `e6c1239d`; exact reviewed-tree/full-check/push evidence is in
the [delivery successor](specs/2026-10-19-durable-execution-evidence.md#de04a-verified-delivery-and-de04b1a-integration-successor).

## Pure decoded durable operations (DE04b1a)

DE04b1a is an additive private pure decoded-operation prerequisite (depends DE02a1), currently IN_PROGRESS. DE04a retains typed current-base documents; DE04b1b depends on DE04a + DE04b1a and retains operation normalization/tracking, current Memory/SQLite deltas with semantic schema2, real checkpoints and atomic publication. DE04/DE04b remain cumulative; DE05 waits full DE04 and DE27 retains history/as-of/fork/migrations. All43 P rows remain OPEN.

See [the pure decoded-operation contract](durable-delta-ops.md) for DE04b1a grammar, ownership and pinned comparison bounds.
