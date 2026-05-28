Independent code review for /Users/jochen/projects/pipy. Do not modify files.

Scope only these files and commands:
- src/pipy_harness/native/terminal_screen.py
- tests/test_native_terminal_screen.py
- scripts/tmux_transient_ui_verify.sh
- docs/harness-spec.md, docs/backlog.md, docs/pi-parity.md, docs/architecture.md
- src/pipy_harness/native/tui.py and src/pipy_harness/native/tool_loop_session.py only as needed to validate integration
- docs/audit/2026-05-28/screen-cell-smoke/sample/summary.tsv, terminal-report.json, anomalies.tsv, and the last line of screen-metrics.jsonl only

Do not traverse docs/audit recursively.

Review against this goal: pipy needs deterministic terminal-screen verification comparable to Pi's packages/tui/test/virtual-terminal.ts, product-path tmux coverage for `uv run pipy repl --native-provider openai-codex --native-model gpt-5.5`, artifacts that locate visible strings and the live/drawn cursor on a terminal grid, and coverage for missing output, prompt visibility, pinned input/footer rows, stale Working rows, diagonal drift, second cursor, and key cell attributes.

Lead with blocking findings only, with file/line references and impact/reproduction. If there are no blocking findings, say that clearly and list non-blocking risks briefly.
