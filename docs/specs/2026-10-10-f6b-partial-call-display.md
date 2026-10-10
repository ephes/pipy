# F6b: live stopped partial-call rows

## Contract

A completed stopped provider message with partial tool calls draws its partial
body and each call as a failed row, using the same stopping reason as restored
history. These are UI decisions only: no execution, canonical tool events or
synthetic persisted results are added. Existing stopped assistant entries and
provider replay exclusion remain unchanged. The active-message guard rejects
late/duplicate completions. Partial argument normalization, interrupted tool
settlement and stopped-text summary inclusion remain separate F6b slices.

## Reproduction and validation

On main at `67e20315`, four production-loop regression cases (failure/cancellation,
valid/incomplete arguments) failed because live history had an error marker where
reopened history had a failed tool row. The implemented reducer reuses the
restored-history presentation and stopping-text helper. Expanded tests also cover
two partial calls, no canonical tool events and exact durable reopen equality.

The first Opus 5.5/high review found that the live call verb started edit
previews and bash clocks for unexecuted calls. The accepted repair adds an
explicit display-only flag, suppressing both; read/edit/bash regression cases
assert no preview or ticker and exact reopen equality. The unchanged first
full check passed 7978 tests with two expected skips. Repair focused validation passes 157 tests; typecheck (657 files), lint,
format and diff checks pass. Full repair `just check` passes 7994 tests with two expected skips; terminal
smoke passes 8 and docs build passes. The isolated real CLI passes failure,
Escape, steering, local command, failed partial read, tree, strict reopen and
continuation in a fresh resumed CLI (both exit 0). A scoped Opus 5.5/high
re-review confirms the code repair but finds the timer assertion insufficient:
a started ticker can be cancelled before the assertion. The final test repair
also checks zero ticker cancellations. The final scoped Opus 5.5/high review is CLEAN (zero findings), closing both
accepted Warnings across three rounds. Final review baseline is
`ccb7519a57a04b115fe3070f7d9d13f66210cb1a`; all reviewed source/test bytes were
compared before commit. Every round was valid, with the reviewer copy removed,
no excluded/skipped files, truncations, redactions or forbidden/denied tool uses.
The second reviewer could not run tests because dependency installation was
denied in its environment; local verification supplies the execution evidence.

Commands: `uv run pytest -q tests/test_native_stopped_tool_call_display.py
 tests/test_native_stopped_turn_display.py tests/test_native_ui_state.py
 tests/test_native_session_history_render.py tests/test_native_agent_event_adapters.py
 tests/test_native_ui_coding_session_renderer.py` (157 passed); `just check`
(7994 passed, two expected skips; lint, format, mypy green); required-runtime
`PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q
 tests/test_native_codemode_composite.py tests/test_native_codemode_delivery.py`
(49 passed); `just test-pty-smoke` (8 passed); `just docs-build`; `just typecheck`;
`uv run ruff check .`; `uv run ruff format --check .`; `git diff --check`.
The final post-full-check test-only assertion was focused-tested (157 passed),
mutation-tested and independently reviewed; production bytes are unchanged.
No pre-commit configuration exists. CLI evidence is private under
`~/.local/state/pipy/stopped-turns-closeout-evidence/2026-10-10-ur899jfg`.
No live-provider coverage is claimed for this deterministic fault test.
