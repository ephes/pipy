# Pinned durable reference verification

The verification tools execute Pi durable **1.3.0**, commit
`2ec55d808d455b1a64a2bf7888198207a096120a`, tree
`b51be844ffaf57e96d06a58d3f45ee90c6ba7401`. DE01a introduced actual Session,
MemoryStorage and document execution; landed DE01b1 added real faux-provider/tool
and context-head scenarios. Landed DE01b2 added SQLite recovery, durable abort and two
exported Node environment scenarios. These verify reference reproducibility and the oracle.
The Python durable comparison adapter is **OPEN** and all P01–P43 rows remain
**OPEN**. No native durable runtime is imported or implemented by these scripts.

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
pushed-job success. DE02 is blocked until b3b is green; every original DE01
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

Version2 `tests/fixtures/durable/coverage.json` contains95 explicit rows:
DE00–DE45, P01–P43 and DE01a/b1/b2/b3/b3a/b3b. Parent DE01 and b3 remain visible.
Each row retains original canonical plan text, owner slices and future acceptance.
P source anchors match the exact corresponding plan source cell; DE source anchors
bind the actual slice contract line (or cumulative child range). Bounded current
case/workload/oracle IDs and appropriate existing evidence links are explicit.
Status must agree with the ledger, with cumulative parent status derived from
its children. b3b has empty reference-family own current evidence; its
inherited_reference field separately attributes existing LOCAL b1/b2/b3a facts,
with no CI/collector/uploaded-job claim. Validators reject omitted,
duplicate or unknown IDs, changed ownership/future/source/status contracts and
dangling or unrelated DE evidence links. Malformed plan rows and absent mutation
preconditions fail as explicit invalid evidence2, with no lookup traceback.
Source-only, reference-only and delivery evidence are distinguished; there is no
native-parity evidence kind. Unexecuted future cases cannot enter the executed
registry. Every P row, including P43, remains OPEN. Reference evidence is partial;
DE02–DE45 still owe their concrete Python behavior, native tests, crash/race/wire
and integration acceptance from the unchanged plan. b3b's real CI and selective
artifact collection remain required before DE02 can start.


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
no remote settings changes in this slice. DE02 stays blocked until real delivery.


The local workflow gate separately executes official pinned actionlint1.7.12
against the complete CI file; optional shellcheck/pyflakes integrations are
disabled for this expression/context check. Permanent host-independent tests
cover the known job-context misuse and compute actual inline child/step/job
budgets. They do not substitute for actionlint or a successful hosted job.
Their YAML parser is an explicit PyYAML dev dependency, locked without changes
to any package version/artifact; ordinary Python tests still require no Node.
