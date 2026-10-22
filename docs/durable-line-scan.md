# Internal durable line scanner

DE32a adds `native/durable/env/line_scan.py`, a private pure prerequisite for a
future `BinaryReader.scan_lines`. It has no filesystem, shell, scheduler or
runtime integration. DE32b retains those adapters and every original DE32
acceptance; DE32 completes only after both children. All P rows remain OPEN.

`LineScanner(start_line, end_line=None)` consumes exact built-in `bytes` through
`push` and returns a frozen, slotted `LineScan` from `finish`. Ranges are zero-based
and half-open; omitted end selects to EOF. Bounds are exact nonnegative integers
at most `2**53-1`; an explicit end must exceed start. Booleans, subclasses,
coercible objects and mutable binary buffers refuse before caller hooks execute.
Empty chunks are accepted. Each scanner is thread-confined; the caller serializes
its use. Python deliberately permits exactly one `finish`: another finish or any
later push raises `RuntimeError`. This lifecycle choice is a Python contract,
not an assertion about upstream reuse semantics.

| Field | Meaning |
| --- | --- |
| `newlines` | Total LF bytes; the file has `newlines+1` lines, including a trailing empty line |
| `start`, `end` | Raw byte selection bounds; exclude the last selected line's terminating LF, retain interior LFs |
| `first_line_end` | First selected LF position or EOF |
| `last_line_start` | Raw start of the final selected line actually present |
| `selected_bytes`, `first_line_bytes` | UTF-8 encoded lengths after replacement decoding of the selection and its first line |

A range beyond EOF has all four offsets at EOF and both decoded sizes zero.
CR remains ordinary data. Only one file-leading EF BB BF is omitted from decoded
sizes; it still occupies raw offsets. Later BOMs remain data, including after an
invalid prefix. Incremental replacement decoding spans arbitrary chunks and
flushes incomplete sequences at each range end. Temporary decoder output is
bounded to 4096 input bytes per block. The scanner retains at most three initial
bytes, two decoder tails and scalar counters, regardless of file or line size.
It never retains the input chunk or selected text.

The target is pinned Pi durable 1.3.0 at
`2ec55d808d455b1a64a2bf7888198207a096120a`, `src/env/line-scan.ts` and
`test/env-line-scan.test.ts`. The Python incremental UTF-8 replacement decoder
is checked against actual Node WHATWG decoding, including malformed partitions.

```sh
PYTHONPATH=src python scripts/durable_env_line_scan_comparison.py \
  --pi-source /Users/jochen/src/pi-mono --output /absolute/new/private/output
```

Use Node26.12.0/npm11.20.0 on PATH. The command uses read-only `git show` to
construct a new minimal private snapshot of seven hash-pinned source/config/test
files, preloads the pinned source resolver and executes the actual scanner and
decoder. The driver refuses unexpected loaded modules; no registry dependency
or npm installation is needed. The existing bounded child runner retains full
logs, uses 120-second deadlines and kills/reaps its group on interruption or
timeout; successful completion performs no later PGID signaling.

The small closed input profile and source-hashed generator reproduce 5000
upstream Mulberry32 seeds. Every byte/range recipe runs with upstream random
chunks and fixed sizes1/2/3/7/65536, including empty chunks: 30000 observations,
compared on all seven fields without normalization. Complete recipes and both
observations stay in the private output, with hashes in `result.json`. Exit0
means equality,1 a valid mismatch,2 invalid evidence or failed prerequisites.
This minimal snapshot is additive pure scanner evidence, not another campaign
bootstrap or full environment conformance result. Root owns the two fresh full
campaign chains and integrated review gates.

Native tests independently derive all raw offsets from LF positions and decoded
sizes from whole-file slices, check lifecycle/exact ingress and test generator
and giant-chunk allocation bounds. Designated private source mutants cover raw
counts, per-block BOM stripping, omitted flush, retained terminating LF,
beyond-EOF offset, first/last confusion and whole-input buffering. The
[evidence ledger](specs/2026-10-19-durable-execution-evidence.md#de32-pre-code-scanner-split)
tracks delivery; [reference verification](durable-reference.md) describes the
broader campaign and its separate closures.

The pinned upstream sources and adapted deterministic generator are MIT, Copyright (c) 2025 Mario Zechner; the [full upstream notice](https://github.com/ephes/pipy/blob/main/tests/fixtures/durable/UPSTREAM-LICENSE.txt) is retained with the reference fixtures.

Output must name an absent directory entry outside upstream. Relative output paths are accepted. Existing entries, including dangling symlinks, refuse without creating their targets.

The source argument is resolved once against the caller's directory and must name a checkout root containing `.git`; relative source paths work. Subdirectory and non-checkout arguments refuse before output creation, and output must remain outside that root. Node response JSON uses the shared closed loader's duplicate-key hook: duplicate top-level or observation-field keys are invalid evidence (exit2), rather than last-value-wins input.
