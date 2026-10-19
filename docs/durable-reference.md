# Pinned durable reference verification

DE01a supplies a verification tool for Pi durable **1.3.0**, commit
`2ec55d808d455b1a64a2bf7888198207a096120a`, tree
`b51be844ffaf57e96d06a58d3f45ee90c6ba7401`. It executes the actual TypeScript
Session kernel, MemoryStorage and typed documents. It does not implement a
Python durable kernel or close any P01–P43 acceptance row. The Python comparison
adapter remains **OPEN**; equality with an expected reference fixture proves
reference reproducibility and the oracle mechanism only.

## Reproduction

Use a POSIX host with Git, tar, **Node v26.12.0 and npm 11.20.0** on PATH,
Python supported by pipy, registry access and a local Git checkout root
containing the exact reference commit. These exact runtime versions are the
version-1 fixture contract, stricter than upstream's Node >=22.19.0 minimum.
No provider credentials, CLI login or paid model calls are used.

```sh
scratch="$(mktemp -d /tmp/pipy-durable-reference.XXXXXX)"
uv run python scripts/durable_pi_comparison.py reference \
  --pi-source /Users/jochen/src/pi-mono --output "$scratch/reference"
uv run pytest tests/durable/test_pi_comparison.py -q
uv run python scripts/durable_pi_comparison.py compare \
  tests/fixtures/durable/kernel-memory-v1.json "$scratch/reference/trace.json"
```

`--pi-source` must name a checkout root, not a package subdirectory or bare Git
directory. `--output` must be a new directory outside the upstream checkout. The parent
must exist. The runner uses owner-only scratch directories, archives the exact
commit with read-only Git operations and installs its original root lockfile
with `npm ci --ignore-scripts --no-audit --no-fund` **inside that snapshot**.
It does not use upstream's working tree, node_modules, build products or CLI.
Every archived file is hashed before installation and checked unchanged after
installation and execution. No checkout, worktree or install runs upstream.

The copied driver runs inside the snapshot with the pinned
`packages/coding-agent/src/experimental/source-resolver.ts` preloaded. This
resolves workspace packages to pinned source, including Telemetry's source
exports. A module hook checks every subsequent non-builtin resolution and
refuses any path outside the snapshot. Workspace imports must resolve under
`packages/` and may contain no `node_modules` path component. Every resolved
non-`node_modules` file must belong to the pre-install archive inventory and
match its exact hash; an extra file under `packages/` is not pinned source.
Third-party resolved modules require physical files and matching installed lock
evidence. The copied driver starts before its module hook and is not a recorded
module: its exact caller artifact hash is pinned separately. The preloaded
resolver is separately checked against its archived source hash. No driver or
instrumentation path receives an exception to the archived-source requirement.

The child receives a small environment with synthetic HOME, npm configuration,
cache and temporary directory; provider secrets and Node injection options are
not propagated. The invoking shell's HOME stays unchanged. The driver uses only
synthetic values and never opens product sessions or private user data. Scratch
is retained deliberately for evidence; remove the directory when its local
review/evidence retention is no longer needed. Commands have explicit deadlines
(900 seconds for installation, 60 for the kernel probe, 120 for other commands);
a deadline or interruption kills and reaps the child process group.

Exit **0** means the actual reference trace matched its pinned expected fixture;
**1** means valid traces differ; **2** means unavailable/invalid evidence or a
failed prerequisite. Malformed lock-package objects, dependency records and
module-resolution path/specifier records raise explicit evidence errors and
return 2; they do not receive the valid-mismatch status 1. There is no
skip-as-pass mode. The standalone `compare`
command only compares traces; it does not independently attest their provenance.
Missing or stale Node/npm, ref, dependency, driver, fixture, module-resolution
or source evidence cannot produce a successful reference run.

## Trace and manifest version 1

The sanitized fixture lives at `tests/fixtures/durable/kernel-memory-v1.json`.
Its envelope has exactly `schema_version`, `case_id`, `normalization`, and an
ordered nonempty `steps` array. Each step has exactly `op` and `value`. The
case is `kernel-memory-v1`, schema integer `1`, normalization `finite-json-v1`.
Unknown versions/fields and boolean or float schema versions reject. Payloads
are finite JSON: duplicate object keys, NaN/Infinity, overflow, non-UTF-8 scalar
strings and integers beyond the interoperable safe range reject.

Only these representation rules apply:

- JSON object key order is immaterial; array/step order remains significant.
- Finite JSON numbers use their common numeric representation (`1` and `1.0`
  compare equally); boolean `true` differs from `1`.
- JavaScript `undefined` is encoded explicitly as `{"$undefined": true}`;
  absent keys and `null` remain different. This marker is reserved by the trace
  codec. No undefined field is silently dropped.
- IDs and commit sequences remain the actual numeric reference values. The
  driver injects a fixed synthetic clock; it does not erase or rebase observed
  timestamps. A future language/ID/time adapter requires a versioned contract.

Version 1 intentionally accepts only this single case. DE01b must introduce an
explicit versioned multi-case manifest and validator dispatch, with registered
case IDs, serializable scenario inputs and per-case observation contracts.
Preserve the v1 fixture's provenance; do not bypass validation or relabel new
Harness/tool/env traces as `kernel-memory-v1`. Chord operations here are reference
trace data, not a requirement for Python's product wire encoding. A real Python
adapter remains a later implementing slice's work.

Errors retain name and message; snapshots, commit publications and Chord
operations retain every captured field. Calls/effects, histories, usage and
causal order must never be discarded to obtain equality. This minimal case has
no provider/tool invocations or usage and therefore proves no such behavior.
Its ten steps include absent read, conversation creation, document commit,
rollback error, retained earlier/current snapshots, stored conversation read,
commit publications and close. It is a small source-derived probe, not a port
of the entire upstream conformance suite.

`manifest.json` pins ref/tree/package, source/lock/license hashes, driver hash,
runtime, case/schema and expected fixture hash. A successful run retains:

- `trace.json`, `resolutions.json` and `run.json`, including fixture/driver/oracle
  hashes, archive hash, runtime, module content hashes and dependency evidence;
- `source-sha256.json`, the complete extracted source inventory, verified after
  installation and execution;
- full stdout/stderr and command/exit/deadline metadata per child, plus the
  exact archive and installed scratch snapshot.

The installed dependency evidence checks package versions, resolved URLs,
integrities and workspace-link targets against the unmodified lock. npm checks
archive integrities while installing. Four precise, already-extraneous records
in the pinned lock (nested typebox 1.1.38 and Anthropic SDK 0.91.1 records) are
copied by npm into its hidden lock but are physically absent. They are retained
under `absent_extraneous_lock_records`, separately from `present`; they are
never counted as installed dependencies. No other missing dependency receives
this treatment, and an actual module cannot resolve through such a record.
Root upstream node_modules is neither inspected nor accepted as reference.

## Provenance and remaining gates

The fixture was recorded from the actual isolated pinned kernel. Its operations
were authored for pipy using the source contracts in
`packages/durable/src/session/session.ts`, `storage/memory.ts`, `documents.ts`,
`test/session-documents.test.ts`, `test/session-support.ts`, and
`src/testing/storage-conformance.ts`. The manifest preserves SHA-256 identities
for core inputs. The precise pin makes all remaining source identities available
without depending on a mutable checkout. The upstream package and source resolver
run from the archived MIT source; the complete upstream notice is retained in
`tests/fixtures/durable/UPSTREAM-LICENSE.txt`: **Copyright (c) 2025 Mario Zechner**.
The synthetic fixture contains no copied user/provider transcript.

Infrastructure tests cover known trace mutations, strict JSON/schema rejection,
missing ref/runtime/dependencies, drift, module escape/missing closure evidence,
private child environment and deadline cleanup. A separate actual reference
command is mandatory; unit tests alone never claim reference execution.

[DE01b](specs/2026-10-19-durable-execution-plan.md#5-delivery-order-and-reviewable-slices)
retains semantic Harness/tool/env scenarios, four required oracle negatives
(lost field, duplicate effect, post-abort write, stale head), complete DE/P
mapping and the hardware/workload baseline. It must also add a pinned explicit
`durable-reference` CI job that executes the real reference without skips and
retains artifacts before DE02 starts. Current ordinary CI checks Python
infrastructure but does **not** execute Node/reference; a green ordinary CI run
cannot satisfy that future gate. No performance, real-provider or Python-native
parity result is claimed by DE01a.
