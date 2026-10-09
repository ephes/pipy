# CM1 delivery and measurement evidence

## T9: production delivery, 2026-10-09

The public production registry, CodingSession/adapter, print/JSON/RPC mode
controllers and installed CPython-on-WASI runtime are exercised by
[tests/test_native_codemode_delivery.py](../../tests/test_native_codemode_delivery.py). Full-product PTY coverage is in
[tests/test_native_codemode_delivery_pty.py](../../tests/test_native_codemode_delivery_pty.py). These tests use the existing scripted
FakeNativeProvider; tools, extension hooks, policy, cancellation, persistence
and terminal rendering are the production paths. They establish A, B and I
at those boundaries, without claiming live-model output quality.

Coordinator verification: runtime-required codemode, nested lifecycle/records,
RPC/JSON, terminal, generation and architecture coverage: **1002 passed in
82.42 s**. Ruff lint/format, mypy (651 files), docs-build and the existing PTY
smoke suite (**8 passed**) also passed. The retained Linux x86_64
`codemode-probes` CI job adds composite, public write-then-raise C and delivery/PTY
tests without removing existing isolation probes. No tests install a runtime
or access a network model.

### Inspected terminal frames

The coordinator used `tmux-transient-ui-verify`, 120-column × 40-row panes,
100 ms sampling, the `pi` theme and isolated config/theme/session paths.
From the integration checkout, each fresh root was launched with:

```sh
env -u VIRTUAL_ENV uv run python tests/codemode_tui_evidence.py --workspace "$evidence_root" --phase success
# Repeat with separate fresh roots and --phase bash / --phase compute.
```

The initial prompt was `summarize fixtures`; after Escape in either abort phase,
`fresh summary` was submitted after the input owner returned. Normal `/exit`
completed the driver's actual persisted-record and worker-group assertions.
HOME was preserved for runtime lookup; no global settings or credentials changed.
The tmux server's inherited unrelated VIRTUAL_ENV was removed only from each
launch environment after discarding an idle initial capture.

| Phase | Frames | Inspected raw frames | Observed transitions |
| --- | ---: | --- | --- |
| Success | 70 | 15 active, 30 settled | `… bash` frames 2–26, `✓ bash` from 27, three `✓ read` from 28; compact count-3 result |
| Bash abort | 80 | 5 active, 16 aborted, settled fresh turn | Actual `bash-active` file before Escape; `… bash` frames 2–15; `⊘ bash` and `Script aborted` from 16; three successful reads afterward |
| Compute abort | 80 | 5 computing, 16 aborted, settled fresh turn | First read settled before guest loop; Escape produced `Script aborted` from 16, retaining read(ok); fresh turn succeeded |
| Saved tree | 40 | startup, 5 and 39 after selection | Replay restored aborted bash and successful reads; `/tree select 0185b6c6` retained cancelled bash and the parent result |

Each anomaly report contains only its header: **zero sampler anomalies**.
Manual inspection checked complete active and settled frames, child placement
inside the parent row, input separators, footer and cursor metrics. For success,
active cursor_y=13 matches the input row 14; settled cursor_y=25 matches input
row 26 (tmux coordinates are zero-based). No stale pending child remained after
settlement or cancellation. The two T9_DONE texts are distinct scripted
assistant messages before and after execution, not duplicated renderer lines.

Normal exits verified all observed worker groups were gone: success had one
parent result/two worker launches; bash and compute each had two parent results
and three launches. Aborted bash persisted cancelled status and its file side
effect; aborted compute persisted the completed read. Saved-tree reconstruction
needed no provider request or manually rendered children.

Raw ANSI/plain frames, summaries, anomaly/cursor metrics, fixture assertions,
product JSONL trees and selected ANSI replay PNGs are retained privately outside
git at `~/.local/state/pipy/codemode-cm1-evidence/2026-10-09-t9/` (0700 directories,
0600 files). Selected PNGs were visually inspected alongside authoritative raw
frames. They are ANSI replay images, not desktop screenshots; an optional Chrome
renderer timed out and was discarded. The evidence intentionally uses a
scripted provider to make active/cancellation phases repeatable. T10 separately
requires live openai-codex measurements and an interactive model run.

## T10: live comparison, 2026-10-09

The coordinator ran an initial pair and a repeat of `scripts/codemode_measure.py` through production
CodingSession/registry, normal extension hooks/budget/persistence and the real
WASI runtime. Both arms used **openai-codex/gpt-6.1-sol, low, SSE**, fresh sessions
and the same 92,468-byte fixture workspace. Only the requested execution style
and selected tool surface differed. Four 400-line files contain eight known KEEP
records among irrelevant DROP lines. A normal fixture-local hook restricts reads
to those four relative paths; no write/bash/edit tools are advertised.

```sh
uv run python scripts/codemode_measure.py \
  --workspace "$evidence/paired-1" --run-label paired-1 \
  --report "$evidence/paired-1.json"
```

These recorded commands omitted `--extra codemode` because wasmtime was already
installed in the checkout environment; for a fresh environment, use
`uv run --extra codemode` as in the driver instructions. `quietStartup` is set
only to keep the interactive capture focused on task output.

The [numeric report](2026-10-09-python-codemode-measurement.json) retains the
platform, runtime pin, configuration and complete metric definitions, with no
request bodies, prompts, auth headers, credential contents or raw transcripts.
Host: macOS arm64, Python 3.14.7, wasmtime 49.0.0, WASI runtime 3.14.7.

| Metric | Direct read | Codemode |
| --- | ---: | ---: |
| Task wall time (seconds) | 10.935 | 6.752 |
| Provider turns / HTTP attempts | 5 / 5 | 2 / 2 |
| Total HTTP body bytes | 254,777 | 10,311 |
| First HTTP body bytes | 3,268 | 4,525 |
| Total serialized input bytes | 240,717 | 2,351 |
| Schema bytes per request | 739 | 1,749 |
| Model input tokens (cumulative) | 59,470 | 2,109 |
| Model output tokens (cumulative) | 225 | 267 |
| Cache-read tokens (cumulative) | 35,328 | 0 |
| Parent result bytes | 4 × 23,117 | 408 |
| Compact codemode output bytes | — | 361 |
| Calls | 4 direct reads | 1 parent, 4 nested reads |
| Exact final facts / expected calls / errors | pass / pass / 0 | pass / pass / 0 |

Codemode's parent duration was **0.242 s**, including its fresh worker and nested
reads. A separate fresh minimal `text(1)` run took **0.167 s**; the preflight
availability check took **0.151 s**. Task timing includes session construction,
selected availability/self-test, model/tool turns, fresh workers and persistence;
it excludes fixture creation and the separately measured preflight/minimal run.
Bytes count actual `json.dumps(body).encode('utf-8')` in every SSE POST, including
retries if any; headers and response bytes are excluded. Schema/input byte totals
are subsets of the body totals, not additional traffic.

The **initial fixed-order pair** observed 95.95% fewer total body bytes and 38.26%
less task wall time with codemode. Its first request was 38.46% larger because
codemode adds a description/schema and task instruction. The direct model chose
one read per turn; repeated accumulated file history accounts for much of its
traffic. Both answers had exactly the eight expected records/order, and codemode
also emitted the exact compact JSON before the final model continuation. There
was no preset target. These samples are descriptive, not a variance estimate or a
general speed claim; task/model choices, batching, caching, network and platform
can change the result. No resident-engine optimization was selected (§6 remains
an owner decision).

### Repeat on the reviewed settings-isolation repair

The initial pair used a shared but empty fixture-local global settings path,
with identical explicit overrides. Review aligned it with the already isolated
per-arm PIPY_CONFIG_HOME, and a regression test pins that path. The coordinator
repeated the same task with fresh fixtures on the repaired script; no performance
target, quality rule or prompt was changed. The
[repeat numeric report](2026-10-09-python-codemode-measurement-repeat.json) is
retained even though its strict execution-pattern check failed.

| Repeat metric | Direct read | Codemode |
| --- | ---: | ---: |
| Task wall time (seconds) | 13.659 | 10.542 |
| Provider turns / HTTP attempts | 5 / 5 | 3 / 3 |
| Total HTTP body bytes | 254,777 | 99,508 |
| Calls | 4 direct reads | 2 parents, 8 nested reads |
| Parent result bytes | 4 × 23,117 | 40,267 then 408 |
| Final exact facts / tool errors | pass / 0 | pass / 0 |
| Prescribed one-parent/four-read pattern | pass | **fail** |

The repeat's codemode model first printed raw read results, then issued a second
script that filtered and emitted the correct compact JSON. The first bulk parent
output was truncated/spilled by normal result delivery. Both scripts and all
reads succeeded; the benchmark exited 1 because the model did not follow the
required single compact script pattern. Final facts remained correct. This is
recorded model behavior, not a provider/runtime failure or a reason to relax the
checker. No further retry was made to obtain a passing number.

The repeat illustrates the limits of the first pair's savings: bulk emission and
an extra model turn increased traffic to 99,508 bytes even with codemode enabled.
Its parent durations were 0.249/0.240 s, minimal fresh worker 0.172 s and preflight
0.192 s. Model choices and network timing vary; these two fixed-order pairs do
not establish a general latency or quality distribution. The separate successful
live interactive run below and initial headless pair establish usable codemode.

### Separate live interactive acceptance

The actual CLI was launched in an isolated 120 × 40 tmux pane with the same
fixture workspace and task, preserving normal credentials/HOME and `pi` theme:

```sh
# Isolated PIPY_CONFIG_HOME, theme/defaults/history/session paths and
# XDG_STATE_HOME; settings contain theme=pi, transport=sse, quietStartup=true.
env -u VIRTUAL_ENV uv run pipy repl --cwd "$evidence/paired-1/codemode" \
  --root "$evidence/interactive/archive" \
  --native-provider openai-codex --native-model gpt-6.1-sol --thinking low \
  --tools read,codemode --approve --no-skills --no-prompt-templates --no-themes
```

The submitted prompt was `prompt('codemode')` from the measurement driver.
The live model wrote one Python script, made four real nested reads and emitted
the exact count-8 compact answer. The final assistant JSON also matched exactly.
The saved product tree has one successful parent result, complete retained read
arguments/statuses and no child results. `/exit` finished normally.

120 frames sampled at 100 ms had zero sampler anomalies. Manually inspected raw
frames 10 (provider working), 30 (three settled children inside the active parent),
60 and 119 (four children, completed parent and final answer) show clean input
separators/footer. Cursor metrics align with the editor (frame 10: cursor_y=12,
input row 13; zero-based tmux coordinates). This run independently establishes
live interactive use and quality; its displayed script time of 0.6 s is not part
of the paired table.

Owned fixture sessions, launch command, prompt, verified assertions and raw
frames are private outside git under
`~/.local/state/pipy/codemode-cm1-evidence/2026-10-09-t10/` (0700 directories,
0600 files). Credentials were neither copied nor reported; no global settings
or theme changed. J and live interactive/headless acceptance are now evidenced.
T11 documentation is implemented; review/check outcomes are recorded separately.


## Acceptance and closeout index

Product implementation T4–T10 and documentation T11 are complete. This index
maps evidence; it does not itself assert a cumulative CLEAN verdict. The
coordinator records per-slice `just check`, docs/PTY checks and independent
review, cumulative whole-campaign review, final CI and main/worktree closeout
separately in the campaign report.

The following are specific executable assertions and recorded results, not an
inference from a generic green check. Paths refer to this repository's tests.

| Item | Evidence and observed result |
| --- | --- |
| A — real multiread/filter | [test_native_codemode_delivery.py::test_public_multifile_summary_and_durable_replay](../../tests/test_native_codemode_delivery.py): exact three-file JSON/count, arguments/statuses, single persisted parent. T9 CI below; T10 initial pair and interactive run above also produced exact eight facts. |
| B — validation/policy | Delivery `test_product_extension_hooks_frozen_builtins` blocks write/transforms read after selection change; `test_public_malformed_and_budget_errors_are_catchable` catches ToolError and continues. T9 runtime-required CI below. |
| C — effects after failure | [test_native_codemode_t8b.py::test_public_session_write_then_raise_retains_effect](../../tests/test_native_codemode_t8b.py): file remains, parent error, write(ok)/not-undone summary. Composite `test_public_definition_write_then_raise_and_spill` also pins records/spill. T9 CI below. |
| D — cancellation in tool | Composite `test_interrupt_active_nested_tool_and_fresh_run` and delivery `test_rpc_live_abort_reaps_and_next_turn_succeeds`; T9 bash frames 2–15 active, 16 aborted, fresh reads successful above. |
| E — infinite loop | Host-runtime `test_an_infinite_loop_ends_at_the_wall_limit` returns timeout; `test_cancel_during_guest_compute` returns abort. T9 compute Escape frame 16 and fresh turn above. |
| F — resource/output bounds | Host-runtime `test_a_memory_blowup_is_recoverable_inside_the_script`, `test_an_uncaught_memory_blowup_still_reports_the_outcome`, `test_a_print_flood_hits_the_output_cap`; result `test_truncation_covers_the_error_and_exposes_the_full_text`; T8a exact owner-only spill and open/write/close failure tests. Runtime-required T9 CI; T10 repeat actually truncated/spilled 40,267-byte bulk parent. |
| G — cleanup | Host-runtime `test_real_runs_leak_no_fds_or_threads`; composite cancellation/fresh-run tests; T9 normal exits verified every observed worker group gone (one/two parents, two/three launches as recorded above). |
| H — isolation | [test_native_codemode_isolation.py](../../tests/test_native_codemode_isolation.py): host filesystem/proc/dev denial, readonly stdlib/symlink confinement, only stdio/preopen fds, native-code/process/thread/socket/DNS/environment/credential denial and group kill. Baseline and expanded runtime-required CI below. |
| I — delivery/resume/tree | Delivery `test_cli_modes_real_delivery` covers print/JSON/RPC parent ids/details and parent-only history; PTY `test_full_tui_driver_and_replay`; inspected success/bash/compute/replay-tree frames and durable assertions above. |
| J — direct comparison | Exact recorded commands, numeric initial/repeat reports and interactive evidence above. Initial pair passes; repeat exits 1 on call pattern despite correct facts/no errors. Two fixed-order samples, not a distribution. |
| K — docs/checks/review | User page, settings/CLI docs, developer reproduction, spike/plan/backlog and release notes updated in T11. Focused T11 checks are recorded below; full checks/independent reviews and cumulative outcomes are recorded in the coordinator campaign report. |

### Retained Linux platform evidence

[Baseline run 37628107203, job 112815030152](https://github.com/ephes/pipy/actions/runs/37628107203/job/112815030152)
at `8ed4e98b`, “Codemode isolation probes (Linux x86_64)”, succeeded:
`PIPY_CODEMODE_REQUIRE_RUNTIME=1`, wasmtime 49.0.0/runtime 3.14.7 compiled cache,
host Python 3.14.8 Linux, **191 passed in 68.72 s**.
[Expanded T9 run 37858973937, job 113589899380](https://github.com/ephes/pipy/actions/runs/37858973937/job/113589899380)
succeeded with **315 passed**, runtime required; all six run jobs succeeded.
These are recorded coordinator-confirmed logs, not final campaign CI.
Intel macOS, Windows and the real Ubuntu host remain unverified. Workspace-write
runtime execution here had transient EPERM signal faults; outer-sandbox evidence
is qualified.

### Slice evidence map

- T4 settle/record extraction (`test_settle_seam_returns_result_and_transition_without_recording`): [test_native_agent_loop.py](../../tests/test_native_agent_loop.py) seam characterization;
  T5 accounting/reservation: [test_native_agent_loop_policy.py](../../tests/test_native_agent_loop_policy.py) and adapters.
- T6a service/frozen identities: [test_native_agent_nested_calls.py](../../tests/test_native_agent_nested_calls.py); T6b real
  worker/session pump: [test_native_codemode_composite.py](../../tests/test_native_codemode_composite.py) and host tests.
- T7a lifecycle/projections: [test_native_nested_lifecycle.py](../../tests/test_native_nested_lifecycle.py); T7b bounded
  durability/replay/summary inputs: [test_native_nested_record.py](../../tests/test_native_nested_record.py) plus delivery.
- T8a definition/CLI/spill: [test_native_codemode_t8a.py](../../tests/test_native_codemode_t8a.py); T8b selection, settings,
  availability and public C: [test_native_codemode_t8b.py](../../tests/test_native_codemode_t8b.py).
- T9 production, PTY and inspected live frames: this note's T9 section and
  [preserved capture instructions](2026-10-09-python-codemode-developer.md#coordinator-terminal-capture).
- T10 live measurements/interactive: this note's T10 section, both numeric reports,
  [test_codemode_measure.py](../../tests/test_codemode_measure.py) instrumentation/privacy/settings-isolation assertions.
- T11 current usage/release notes and acceptance index: docs-only; no runtime or
  stable model-description change. Gate outcomes are recorded separately.

### Stdlib evidence

Coordinator real-WASI probes on installed 3.14.7 imported json, re, math,
statistics, itertools, functools, collections, datetime, pathlib, csv, random,
hashlib, decimal and fractions successfully. subprocess/socket/threading imported,
but operations were unavailable; isolation tests pin process/socket/thread denial.
ssl/ctypes/zlib/gzip/bz2/lzma/sqlite3 imports returned ModuleNotFoundError.
Source-safe command/results are retained in campaign scratch `stdlib-probe.txt`
and `stdlib-operation-probe.txt`; no host authority follows from an import.

### Remaining owner decisions

Extension tools, MCP, parallel calls, images, models/store/load/options lines and
structured bash are separate follow-ons. Resident engine requires an owner
decision after T10, with no measured benefit claimed for production. Runtime
provenance/mirroring/packaging and platform gaps remain open. Per-call CustomEntry
crash durability requires an owner decision: returning failures persist parent
records, but a mid-script host crash or canonical pipeline exception has no such
guarantee. T10's repeat is retained model-adherence evidence, not retried away.


### T11 focused verification

- `just docs-build`: passed, no issues (initial 5.31 s; final rebuild 0.95 s).
- `uv run pytest -q tests/test_native_codemode_t8a.py tests/test_native_codemode_result.py tests/test_native_codemode_t8b.py -k 'not public_session_write_then_raise'`: **87 passed, 1 deselected** (0.66 s). Public C is retained in CI and separate runtime delivery coverage; this focused run checks definition/CLI/selection/truncation contracts.
- User-page JSON example parsed with `json.loads`, checked to contain only `code`,
  compiled, then executed in the installed real WASI worker with two host read
  callbacks over temporary alpha/beta fixtures: no error, compact count=2. This
  checks the example, not an additional production-session acceptance claim.
- Repeated the documented stdlib import probe through the real WASI `run_script`:
  all 17 listed available/import-only modules imported; all seven unavailable
  modules returned ModuleNotFoundError, matching the coordinator record.
- `PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q tests/test_native_codemode_host_runtime.py tests/test_native_codemode_isolation.py tests/test_native_codemode_delivery.py`: **43 passed in 21.50 s**, covering real limits/flood/no-leak/isolation and public summary/hooks/RPC abort with fresh-turn cleanup.
- Local links and `git diff --check` passed. No Python source changed; no new
  backend/feature, global settings/theme or credential writes. This focused
  verification performed no review, commit or push.

Full `just check`, independent T11/cumulative review and final main/CI outcomes
are recorded separately in the coordinator campaign report.
