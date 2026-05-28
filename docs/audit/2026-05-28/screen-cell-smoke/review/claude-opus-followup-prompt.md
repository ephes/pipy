Follow-up independent review for /Users/jochen/projects/pipy. Do not modify files.

Review only the post-review hardening changes:
- `src/pipy_harness/native/terminal_screen.py`: `analyze_frame_files` now accepts explicit `columns`/`rows`, CLI args `--columns`/`--rows`, and forwards them to `parse_ansi_screen`.
- `scripts/tmux_transient_ui_verify.sh`: the tmux pane geometry is named as `PANE_COLUMNS=100` and `PANE_ROWS=30`, used for both tmux startup and the analyzer invocation.
- `tests/test_native_terminal_screen.py`: negative regression coverage now asserts duplicate/stale `Working...` and missing expected model output produce anomalies.
- Latest product-path evidence: `docs/audit/2026-05-28/screen-cell-smoke/sample/summary.tsv` reports `settled=yes`, `anomaly_count=0`; `terminal-report.json` reports `anomaly_count=0`; the last `screen-metrics.jsonl` line locates prompt row 15, expected output row 18, footer/status rows 23/24, reverse cursor row 21, live cursor y 22, and `cursor_matches_input_row=true`.
- `just check` passed after these changes.

Lead with blocking findings only. A blocking finding is one that means the verifier no longer exercises the product path or can no longer detect missing output, prompt visibility, pinned input/footer rows, stale Working rows, diagonal drift, second cursor/cursor drift, or key cell attributes. If there are no blocking findings, say that clearly.
