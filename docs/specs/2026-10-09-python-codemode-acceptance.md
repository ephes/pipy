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

## T10: live comparison

Pending. Compare the same task with direct tools and codemode, recording actual
request bytes, wall time including worker startup, compact output and expected
facts without a preset performance target. The resident-worker optimization
remains deferred.
