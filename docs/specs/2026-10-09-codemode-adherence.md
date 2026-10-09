# CM1-A1: codemode adherence guidance

Prepared on main after `3d15c7a2`. Scope: the advertised builtin description and
its selected-tool system guidance. Codex is the sole writer. Implementation and
review created no product commit or push; the owner subsequently authorized
commit and push at closeout.

## Reproduction and decision

The owned synthetic DF1-F5b live acceptance retained three correct codemode
answers, but only one met the prescribed one-parent/four-read pattern. Inspecting
those controlled scripts showed a deliberate exception containing a raw read to
probe its format, a type-printing probe and a separate bulk-output script. They
were model choices, not sandbox failures. No unrelated transcript or credentials
were inspected.

A fresh baseline at the same main commit used three fixed live samples through
the existing CM1 measurement helpers and unchanged strict checker. Only one of
three passed the strict pattern; all three returned correct final facts. This
reproduces the current guidance limitation, not a deterministic guarantee of
model failure. Every sample uses a fresh synthetic workspace and isolated state,
openai-codex / gpt-6.1-sol at low effort over SSE. Samples are fixed before the
change, not retried until success. Reports stay private outside git.

Choose a prompt-only repair: clarify that returned values are strings, show
splitlines-based parsing of small text files, keep intermediate results within
Python and emit the compact answer after filtering. The current description
already names the return type but supplies no runnable example; the selected-tool
guideline alone says to chain calls or filter large output. The new example is
also present in the actual advertised definition and tested through the product
pipeline. It deliberately uses two small files, not the four-file live benchmark.

## Contract and boundaries

No runtime, schema, tool authority, admission, hook, budget, cancellation, retry,
persistence, nested-record or output-spill behavior changes. Tools retain their
normal direct-call arguments and model-facing text, including extension result
transforms and read pagination/truncation notices. JSON parsing is appropriate
only when the content is actually JSON. The small-file example does not implement
pagination; the description and user docs direct callers to follow read notices.

One script is a recommendation when practical, not a cardinality rule. Legitimate
multi-step work and debugging remain possible. No script effects are rolled back,
no result format becomes structured, no probe heuristic is enforced and no model
adherence or general speed/quality improvement is guaranteed. Existing historical
strict-pattern failures remain visible. The checker, task, expected facts and
requested provider/model/effort stay unchanged in the new live samples.

## Verification

The new advertised-example regression was run before editing production code:
`PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q tests/test_native_codemode_delivery.py::test_advertised_read_filter_example_runs_without_bulk_output`
failed because the baseline advertised no executable example. This is a guard
for the new guidance contract; the live fixed samples reproduce the adherence
issue. It is not an offline test of model behavior.

With the guidance implemented, the example and definition tests passed (2 tests).
The example test takes code from the actual description, verifies advertisement,
runs real WASI reads through the public product API, asserts exactly one parent
and two reads, exact filtered output without bulk text, and parent-only nested
record preservation through strict durable reopen.

The three fixed modified-instruction samples all passed the unchanged strict
checker: one parent, four reads and exact compact/final facts in every run. Baseline
parent/read counts were 1/4, 2/8 and 3/12; modified counts were 1/4 in all three.
Task prompt hashes match between arms; no checker, expected answer or task was
changed. These sequential samples are descriptive, not a statistical comparison
or guarantee. No additional run was requested after observing the results.

The private sample driver used normal live provider requests but did not connect
its separate HTTP byte-counter wrapper to the provider. Its zero HTTP-byte fields
are therefore unmeasured placeholders, not zero traffic; no performance claim is
made. Canonical tool counts and exact-fact checks remain the unchanged checker.
State and synthetic product sessions remain outside git in owned directories.

Ruff, formatting and mypy (654 files), docs build and 174 focused required-runtime
contracts passed. PTY smoke passed 8 tests. The first full check found the old
codemode-only pinned guideline expectation (1 failed, 7,940 passed, 2 skipped);
the expected extra guideline is now pinned without changing established Pi
assertions. The repaired focused group passed 186; the full `just check` passed
7,941 with two expected skips, process exit 0. No pre-commit configuration exists,
so no prek hooks are required. Post-review evidence-only documentation changes
are checked with docs build and `git diff --check` before closeout.

Commands run from the repository:

```sh
PIPY_CODEMODE_REQUIRE_RUNTIME=1 uv run --extra codemode pytest -q tests/test_native_codemode_delivery.py tests/test_native_codemode_t8a.py tests/test_native_codemode_t8b.py tests/test_native_codemode_composite.py tests/test_native_codemode_isolation.py tests/test_codemode_measure.py tests/test_native_system_prompt_sections.py tests/test_native_system_prompt_pi_pinned.py tests/test_native_compaction_summary_recovery.py
uv run ruff check .
uv run ruff format --check .
just typecheck
just check
just docs-build
just test-pty-smoke
git diff --check
```

## Independent review and disposition

The installed supervised Claude harness completed one whole-slice review with
`claude-opus-5-5` at high effort. The valid verdict is ISSUES with one Suggestion,
zero Critical or Warning findings. There were no skipped files, truncations,
redactions, denied or forbidden tool use; the private review copy was removed.
Reviewed baseline snapshot: `476b925f1df6c0b82361578e12c7774a2adceb4a`;
reviewed tree: `0ec42b6bb6aa9ddf5cf2e7f44ea50e8171e24fda`. Review began while the
frozen full rerun was in flight; pending evidence was explicitly disclosed and
the process subsequently exited 0 before documentation closeout.

The Suggestion asks to pin every descriptive sentence verbatim in the definition
test. Defer that as low value: the example runs through production, the selected
guideline is pinned, and key string/compact-output semantics are asserted.
Duplicating the remaining wording mainly turns editorial changes into test
failures rather than detecting a runtime or current product defect. The text's
pagination and practical-single-script qualifications remain explicit in this
reviewed description and user docs. The harness reports convergence; another
round would seek wording agreement rather than reduce a demonstrated risk.

Closure is **advisory, not CLEAN**. No unaccepted blocking finding remains.
Source/tests, user docs and release notes remain byte-identical to the review
snapshot. Only this evidence note and backlog status are finalized afterward.
During implementation/review, no repair review, model substitution, extra
implementation agent, branch, product commit or push occurred.

## Workflow and remaining limits

Summary-safe archive searches and Markdown summaries were read first. Codex drove
the implementation (exact session model identifier is not exposed), with no extra
implementation writers. Summary-safe decisions, fixed-sample results, the pinned
expectation repair, verification and actual reviewer model/effort/disposition are
recorded in the dedicated `cm1-codemode-adherence` archive record. Raw fixture
sessions and private sample reports remain outside git, with owner-only access;
no raw prompt, summary, output or credentials enter workflow events.

The private driver and reports are retained at the owned codemode-adherence
evidence root. Normal auth manager access was used; credential bodies were not
inspected or manually written. Global settings/theme are unchanged and remain
pi; config, theme/defaults/history, session and state paths were isolated.

Only the selected openai-codex read/filter task has this new live evidence. No
other model/provider, general single-script reliability, performance, large-file
pagination implementation or codemode expansion is established. Full historical
failure evidence remains applicable. The owner subsequently authorized committing and pushing this slice.
