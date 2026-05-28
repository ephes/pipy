You are doing a follow-up review in /Users/jochen/projects/pipy.

Use a code-review stance: findings first, ordered by severity, with concrete
file/line references. Focus only on whether the previous blocking findings are
fixed and whether the fixes introduced new regressions.

Previous Opus review blocking findings:
- B1: terminal_compare was positional-only and ignored cell attributes; the
  smoke already had an unflagged loader color mismatch.
- B2: the product-path smoke used only `Reply exactly: Hello!`, so it did not
  exercise sustained transient/tool rendering.

Fixes made after that review:
- `src/pipy_harness/native/terminal_compare.py` now records
  `attributes_match`, `reference_attr`, and `target_attr`; attribute mismatch
  creates an error anomaly, and default final row/column tolerance is zero.
- `src/pipy_harness/native/tui.py` renders `working` rows with Pi-like
  secondary dim styling.
- `src/pipy_harness/native/terminal_screen.py` reports multiple reverse cursor
  cells.
- `scripts/tmux_transient_ui_verify.sh` and
  `scripts/tmux_pi_comparison_verify.sh` now require expected output unless
  derivable from `Reply exactly: `, and sample active frames at 100 ms by
  default.
- A tool-directed product smoke exposed a real overflow bug: long tool output
  pushed the input/footer frame off-screen and hid the prompt. `tui.py` now
  tails history before adding input/footer and preserves the latest user block
  during overflow.
- Tests were added for attribute mismatch, missing required final metrics,
  duplicate reverse cursor cells, and overflow preserving prompt/input/footer.

Current evidence:
- `just check`: 983 passed, 2 skipped.
- Pi comparison smoke:
  - `docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/comparison-report.json`
  - `docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/comparison-anomalies.tsv`
  - `docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/row-column-deltas.tsv`
  - pipy/pi summaries under `docs/audit/2026-05-28/pi-comparison-smoke/sample/`
- Tool-directed product smoke:
  - `docs/audit/2026-05-28/tool-stream-smoke/pipy/summary.tsv`
  - `docs/audit/2026-05-28/tool-stream-smoke/pipy/anomalies.tsv`
  - `docs/audit/2026-05-28/tool-stream-smoke/pipy/screen-anomalies.tsv`
  - `docs/audit/2026-05-28/tool-stream-smoke/pipy/screen-metrics.jsonl`

Review these files in particular:
- src/pipy_harness/native/terminal_compare.py
- src/pipy_harness/native/terminal_screen.py
- src/pipy_harness/native/tui.py
- scripts/tmux_transient_ui_verify.sh
- scripts/tmux_pi_comparison_verify.sh
- tests/test_native_terminal_compare.py
- tests/test_native_terminal_screen.py
- tests/test_native_tool_loop_tui.py
- docs/harness-spec.md
- docs/backlog.md
- docs/pi-parity.md
- docs/architecture.md

Questions:
1. Are B1 and B2 fixed strongly enough for the stated TUI verification goal?
2. Does the overflow/prompt-preservation fix keep input/footer pinned without
   hiding the latest user prompt or final output in the real product path?
3. Are there any remaining blocking findings about hidden output, prompt
   retention, footer/input pinning, cursor detection, product-path coverage,
   or Pi comparison artifacts?

If there are no blocking findings, say that clearly and list any non-blocking
risks separately.
