You are reviewing a dirty worktree in /Users/jochen/projects/pipy.

Use a code-review stance: findings first, ordered by severity, with concrete
file/line references. Focus on correctness, product-path coverage, regression
risk, missing tests, and stale docs. Do not summarize unless there are no
findings.

Context:
- Goal: pipy's TUI verification must be at least as strong as Pi's pi-mono
  TUI verification before further visual parity work.
- Earlier Opus reviews of the screen-cell verifier found no blocking findings.
- This follow-up adds the Pi comparison layer and final product smoke evidence.
- The user explicitly requested Opus, not Sonnet.

Review these implementation files:
- src/pipy_harness/native/terminal_screen.py
- src/pipy_harness/native/terminal_compare.py
- src/pipy_harness/native/tui.py
- src/pipy_harness/native/tool_loop_session.py
- scripts/tmux_transient_ui_verify.sh
- scripts/tmux_pi_comparison_verify.sh
- tests/test_native_terminal_screen.py
- tests/test_native_terminal_compare.py
- tests/test_native_tool_loop_tui.py
- tests/test_native_tool_loop_streaming_and_rendering.py
- docs/harness-spec.md
- docs/backlog.md
- docs/pi-parity.md
- docs/architecture.md

Important smoke artifacts:
- docs/audit/2026-05-28/pi-comparison-smoke/sample/pipy/summary.tsv
- docs/audit/2026-05-28/pi-comparison-smoke/sample/pi/summary.tsv
- docs/audit/2026-05-28/pi-comparison-smoke/sample/pipy/screen-metrics.jsonl
- docs/audit/2026-05-28/pi-comparison-smoke/sample/pi/screen-metrics.jsonl
- docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/comparison-report.json
- docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/comparison-anomalies.tsv
- docs/audit/2026-05-28/pi-comparison-smoke/sample/comparison/row-column-deltas.tsv

Verification already run:
- scripts/tmux_transient_ui_verify.sh docs/audit/2026-05-28/screen-cell-smoke/sample 'Reply exactly: Hello!' 'Hello!'
- scripts/tmux_pi_comparison_verify.sh docs/audit/2026-05-28/pi-comparison-smoke/sample 'Reply exactly: Hello!' 'Hello!'
- uv run pytest tests/test_native_terminal_compare.py tests/test_native_terminal_screen.py
- uv run ruff check src/pipy_harness/native/terminal_compare.py tests/test_native_terminal_compare.py
- uv run mypy src/pipy_harness/native/terminal_compare.py tests/test_native_terminal_compare.py
- bash -n scripts/tmux_pi_comparison_verify.sh
- just check: 980 passed, 2 skipped

Specific questions:
1. Does the terminal-screen harness actually prove visible prompt/output,
   pinned input/footer rows, cursor alignment, stale Working rows, and cell
   attributes through the real command path?
2. Does terminal_compare compare the correct frames, especially final-to-final
   when Pi and pipy settle after different active-frame counts?
3. Are the Pi comparison artifacts strong enough to catch row/column drift,
   hidden output, duplicate cursor, footer overwrite, and stale Working rows?
4. Are any docs or tests still stale relative to the implementation?

If there are no blocking findings, say that clearly. Also list any
non-blocking risks separately.
