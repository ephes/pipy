# CM1 delivery and measurement evidence

## T9: production delivery, 2026-10-09

The public production registry, CodingSession/adapter, print/JSON/RPC mode
controllers and installed CPython-on-WASI runtime are exercised by
`tests/test_native_codemode_delivery.py`. Full-product PTY coverage is in
`tests/test_native_codemode_delivery_pty.py`. These tests use the existing scripted
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
T11 final documentation and cumulative review remain pending.
