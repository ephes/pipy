# OpenAI-Codex Parity Runner Defense Plan

Date: 2026-07-13

## Gap

OpenAI-Codex transport reliability slice 4: parity-runner defense-in-depth for the historical raw pipy timeout diagnostic and structured child-attempt accounting. The provider remains the primary owner of timeout normalization; the runner only recognizes a narrow child-log tail line when a legacy/raw failure escapes the child process.

## Pi / reference behavior and ownership

Pi reference for this operator-selected reliability gap is the existing transport reliability research/design and implementation plan under `docs/specs/2026-07-13-openai-codex-transport-reliability-*` plus `docs/plans/2026-07-13-openai-codex-transport-reliability.md`. This slice is pipy-owned runner reliability, not a provider request-shape or Pi API surface. The exact historical raw line to recognize is the stripped child-log tail line `pipy: The read operation timed out`. The already-shipped normalized line remains `pipy: provider failure during turn:`. Both are retryable only as `provider_failure` and only when branch, HEAD, refs, and worktree are unchanged.

## Implementation plan

1. Add a `ChildRunResult(exit_code, stdout, timed_out)` dataclass and update runner hooks, real spawn helpers, and tests. `_spawn_capture()` sets `timed_out=True` only from `subprocess.TimeoutExpired`; negative exit codes alone must not imply timeout.
2. Extend `child_block_reason()` to inspect only non-empty lines from the last 20 log lines and classify exactly the stripped line `pipy: The read operation timed out` as `provider_failure`. Do not broaden matching to arbitrary timeout/OSError wording, payload-like text, near matches, or earlier log content.
3. Emit `gap.attempt_started` immediately before every child gap invocation and `gap.attempt_finished` for every result. Fields stay summary-safe: index, attempt, exit-code value, timed-out boolean, and bounded outcome/reason; never duplicate child log body.
4. Keep retry refusal guards unchanged and explicitly cover dirty tracked file, untracked file, branch change, HEAD change, ref change, and baseline-dirty preflight behavior.
5. Update `docs/parity-loop/parity-runner.md`, backlog, audit, and the transport reliability plan to describe the runner-defense slice as shipped while leaving final documentation/integration closure as the remaining slice.

## Done when

- `tests/test_parity_runner.py` covers normalized and legacy retry, negative legacy matches, child timeout versus signal exit, attempt event ordering, retry exhaustion, retry success, retry skipped after progress, and no-progress guard variants.
- Focused provider/product regression suites still pass unchanged.
- `uv lock --check`, focused tests, `git diff --check`, and `just check` pass.
- Different-family review returns CLEAN over the final code and docs diff before commit.
