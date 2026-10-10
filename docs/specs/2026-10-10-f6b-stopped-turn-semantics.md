# F6b stopped-turn semantics closeout

## Design

This bounded follow-up closes the three remaining F6b content/lifecycle gaps.
Partial stopped call arguments use the dependency decision in
[the ADR](../decisions/2026-10-10-stopped-partial-json.md). Ordinary successful
calls and validation remain unchanged; normalized stopped calls never execute.

An operator or local-command interruption during tools first settles the active
call and every skipped sibling result, preserving pairing. Then a new assistant
lifecycle records an empty aborted assistant, with zero usage and the request's
provider/model identity. It makes no additional provider request and does not
classify a tool interruption as a provider cancellation. One cancellation event
belongs to this terminal assistant; queued-input and abort clearing are unchanged.

The shared private summary builder projects stopped partial text into chronological
labelled assistant excerpts carried as user data, marked unfinished and aborted
or `error`, using the canonical stop-reason labels. It never replays the stopped assistant/call payloads or includes its thinking.
Bounded attempted-file metadata deliberately retains recovered partial stopped
paths, following Pi and CM1's attempted-operation contract. Its framing explicitly
warns that an intent may never have executed and its path may be partial. At the
canonical text cap, projection uses at most two consecutive labelled data messages
without dropping any text; their entire framing is counted by normal admission.
Ordinary provider requests still exclude stopped messages. Both compaction and
branch summaries use this projection, without changing their budget, privacy,
cancellation, ownership/currentness or state-first persistence contracts. F5b
recovery still admits the whole request and retains its durable reduction warning.
Saved transcript entries are append-only. Failed/stale/cancelled summary work
never publishes partial accepted compaction.

## Baseline

At `2ad2a1fb`, the production SDK/CLI regression fails 25 cases for the intended
reasons: 22 stopped-argument cases retain invalid/raw values, the interrupted-tool
run ends at its skipped result, and two manual compaction cases omit stopped text.
Expanded baseline: 36 intended failures and six passing complete-value
preservation cases. The actual local Pi parser independently matches nine finite
prefix/repair cases; complete versus partial null is explicitly distinguished.
Plan/dependency review: two valid Opus 5.5/high rounds, one Warning and three
Suggestions accepted as contract/test clarifications, scoped repair CLEAN.
Both copies were removed; no skipped files, truncations, redactions or forbidden/
denied tool uses. Final plan baseline: `4189a7f35a4a7ec6e93bd61efc049112c33e0bd4`.

Implementation passes 207 focused semantics/loop/reasoning/display tests and
454 architecture/compaction/privacy/budget/persistence contract tests. Typecheck
passes 659 files. A narrow local dependency stub addresses the upstream missing
`py.typed` marker without relaxing strictness. The first full implementation check completed with two failed contract tests,
8034 passed and two expected skips: the obsolete summary-drop assertion and the
closed-set typing configuration assertion. Both are repaired; the latter now
pins the narrow stub path and prevents product-package shadowing without removing
any strict frontier checks.

The first valid Opus 5.5/high code review reported three Warnings, all accepted:
the obsolete summary test, a canonical-cap label overflow before recovery, and
undocumented stopped attempted-file metadata. A new cap regression reproduces
four failures and two preservation passes before repair. Repair splits full text
without loss and tests whole-budget automatic recovery at the cap. Metadata is
truthfully framed/documented/tested, preserving existing attempted extraction.
Initial repair-focused tests pass 87; the combined final contract command
passes 686, including all new semantics, prior stopped-turn, typing, architecture,
compaction/budget/privacy, cancellation/stale/persistence and nested-record tests.
Typecheck remains green. Final full repair check completes successfully: **8046 passed, two expected
skips**, with lint/format and mypy green (659 files). Required runtime checks pass
49 and PTY smoke passes 8. Docs build and diff check pass. No pre-commit
configuration exists.

Code review closes **advisory, not CLEAN**, after two valid Opus 5.5/high rounds.
All three accepted Warnings are repaired. Round two leaves one Suggestion:
clarify that diagnostic repr assertions check redaction, not summary exclusion.
A comment addresses it; test logic and production code are unchanged. The harness
reports convergence, so further review would add no material value. No blocking
or unresolved findings remain. Both reviewer copies were removed; no skips,
truncations, redactions, forbidden/denied tools or lifecycle failures. Final
review baseline is `7df23beb6699b0a449927ba323d8d734f33fb2e8`; all implementation,
test, stub and config bytes matched before the post-review comment/docs updates.

Isolated real CLI acceptance passed provider failure, Escape, steering, local
command, failed partial read, interrupted shell, later continuation, manual
compaction with stopped text, tree, strict reopen and resumed continuation; both
CLI instances exited zero. Evidence: private
`~/.local/state/pipy/stopped-turns-closeout-evidence/2026-10-10-3o_n6fqd`.
A separate authenticated `openai-codex/gpt-6.1-sol` CLI run at medium thinking
performed codemode plus a nested read and returned the expected marker, exit zero
and zero stderr, with a complete nested record and parent-only canonical result.
Its summary-safe evidence is at `2026-10-10-live-akoaw8g_` under the same private
root. No credential contents were inspected/copied or global settings/theme manually
changed. Repeated final CLI fault acceptance after the code repair is at
`2026-10-10-7bpvae7y` (both exits zero).

A separate final real CLI pressure test uses a capped stopped answer and old
large paste, then an intrinsically oversized new paste. Every auxiliary request
is independently checked against the 10000-token ceiling including a 300-token
reserve. Recovery warns visibly and durably, repeated fitting prompts continue,
the oversized new prompt refuses normally, a later fitting prompt continues
without `/new`, strict reopen/resume succeeds and old branch entries remain.
Both CLI instances exit zero; evidence is `2026-10-10-rnnrsdya`. Instrumentation
repairs used an explicit startup local-command handshake and a named tmux paste
buffer (tmux rejects huge command arguments); no product fix was needed.

## Final verification commands

```sh
uv run pytest -q tests/test_native_stopped_turn_semantics.py tests/test_native_aborted_turn.py tests/test_typing_config.py tests/test_native_agent_loop.py tests/test_native_reasoning_replay.py tests/test_native_stopped_tool_call_display.py tests/test_architecture_import_boundaries.py tests/test_architecture_quality_gates.py tests/test_native_compaction_oversized_turn.py tests/test_native_semantic_compaction.py tests/test_native_semantic_compaction_retry.py tests/test_native_request_budget.py tests/test_native_request_budget_admission.py tests/test_native_session_tree_anchored_compaction.py tests/test_native_session_tree_self_compaction.py tests/test_native_branch_summary_retry.py tests/test_native_nested_record.py tests/test_native_compaction_summary_recovery.py
PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q tests/test_native_codemode_composite.py tests/test_native_codemode_delivery.py
uv run ruff check .
uv run ruff format --check .
just typecheck
just check
just docs-build
just test-pty-smoke
git diff --check
```

The combined focused command passes 686, runtime 49, full check 8046 plus two
expected skips, and PTY smoke 8. Commands were awaited through process exit, and
source stayed frozen. After the advisory comment/docs update, the 14 aborted-turn
tests and lint/format/type/docs/diff checks are repeated before commit.

## Closeout scope

All remaining F6b slices are complete. F4b retry-attempt persistence/visibility,
F7b polish and the broader backlog remain separate work. No other provider-family
live coverage or general partial-parser equivalence is claimed. Non-finite
argument fallback is documented; future dependency/stub updates require normal
review. Summary estimates remain conservative rather than provider guarantees.
Existing generation-failure/cancellation/stale/currentness and state-first,
escaping persistence-failure tests pass without rollback or automatic retry.
CM1 pairing, attempted extraction, bounded nested records and parent-only
persistence remain covered. Summary content stays private; workflow events are
summary-safe. Codex owns implementation (exact session model is not exposed),
with no additional implementation agents. Commits/pushes are owner-authorized;
no branch, PR, release or publication was created.
