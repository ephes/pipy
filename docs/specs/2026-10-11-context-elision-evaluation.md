# Context elision microevaluation

Owner-requested evaluation inspired by [An Empirical Study of Harness Design
for Coding Agents](https://arxiv.org/pdf/2609.20804). This is a synthetic
microevaluation of a request projection, not a product policy change or a
SWE-Bench replication. The result supports keeping current compaction: generic
elision lost transient evidence and increased uncached input in these fixtures.

## Reproduction and fixed method

```sh
uv run python scripts/context_elision_measure.py --report /tmp/pipy-elision.json
```

Requires existing pipy `openai-codex` authentication; selects `gpt-6.1-sol`,
low effort and SSE, with no provider/model fallback. Four pairs per case run
current/elision, elision/current, current/elision, elision/current. Each pair
uses identical seeded history and ground truth, fresh workspaces, independent
session IDs and isolated state. Each session receives a retention question,
then a request to repeat the same answer as a follow-up cost/cache probe. The
repeat is not an independent retention observation. Settings and the
native theme in the real home directory remain untouched. Private product
trees are retained under the temporary directory printed on stdout; its path
is excluded from the published counter-only report. No old user transcripts
or repository contents are submitted.

The artificial deployment ceiling is 24,000 estimated tokens with a 2,048
output reserve. This exercises pipy's real admission and compaction path; it
does not restrict the remote model's actual context window. History consists
of eight synthetic completed read cycles with roughly 10 KB of output each.
These cycles are seeded through native append APIs, not produced by the live
model. Their initial generation/read cost is excluded equally from both arms.

- **Current:** unchanged pipy request preparation and automatic compaction.
- **Elision:** an evaluation-only patch of `_RequestPreparationEffects._request_values`
  projects the provider request before its admission estimate. Above 60% of
  estimated input allowance, successful read bodies longer than 2,048 characters
  are replaced by short omission notices, except the last two complete tool
  cycles. Prose, user constraints, call identities, result pairing and errors
  are preserved. Canonical history and durable entries are unchanged; any later
  semantic summary still uses original history with pipy's existing summary
  input truncation. This differs from summarizing an already elided transcript
  and from the paper's external recall store.

The script uses a private synchronous process; its monkeypatch is not suitable
as a concurrent production implementation. Normal admission, summary execution,
provider retry and persistence remain in the native runtime. Both arms have the
same tool exposure per case. Only the reread case exposes a tool, with the
unchanged builtin read definition and execution restricted to `log-0.txt`.

## Cases and metrics

| Case | Question | Recovery route |
| --- | --- | --- |
| Bulk | Recall two constraints from the initial user message | Preserved prose or semantic summary |
| Reread | Recall a random code in the first historical read | Semantic summary or reread the fixture |
| History only | Recall that code after its file is absent | Semantic summary; elided output cannot be reread |

The random recovery code is placed at the beginning of the first output so it
survives baseline summary-input truncation; it is absent from the question,
assistant prose and filenames. The history-only case deliberately tests a
loss-of-evidence boundary. Its correct answer requires the code, while returning
`null` is recorded separately as honest uncertainty, not successful retention.

Report strict JSON correctness separately for retention and repeat submissions,
private summary operations,
ordinary provider operations, HTTP attempts and serialized bytes, actual
provider input/output/reasoning/cache counters including summaries, rereads,
tool errors, total wall time and original-history/strict-reopen invariants.
If any run has incomplete attempt usage, its group's token totals and uncached
comparison are reported unavailable; partial counter sums are not group totals.
Bytes and heuristic admission estimates
are not actual token counts. Subscription counters do not imply an incremental
dollar charge. No retries are made merely to obtain a better answer.

Compare paired differences per case and retain failed/unknown answers. Four
pairs are descriptive evidence only. These short recall tasks cannot establish
general coding success, long-session cost, optimal thresholds, or behavior with
other providers. One summary may amortize over many later requests, while
elision may retain substantial recent output. Count every provider request,
including the private summary, rather than interpreting avoided summaries alone
as savings. Cache affinity is independent for each fresh session; cross-session
cache isolation cannot be guaranteed by the endpoint. Both the cache-hit rate
and uncached comparison depend on the number of follow-up submissions: this
study includes exactly one repeat. Current post-summary requests are much
shorter and recorded no cache hits; elision requests generally recorded a hit
on repeat.
Full repository validation ran concurrently with the live measurements, so
elapsed times include host-load noise. These samples never triggered fallback
summarization in the elision arm; that combined-stage behavior is unmeasured.

## Results

The fixed run completed on 2026-10-11 against main `e523b1b9` (the complete
commit ID and driver SHA-256 are in the
[counter-only measurement](2026-10-11-context-elision-measurement.json)). All
24 sessions completed without admission/provider failures or tool errors.
Every original history was preserved byte-for-byte as a prefix, no omission
stub was persisted, and every tree reopened strictly. Global theme/settings
hashes were unchanged. All five requested provider usage counters were reported
for every observed attempt, including private summaries. There were no extra
HTTP retries in this run.

Each row below covers four sessions with four independent retention questions
and four repeat probes. Wall time is the median per session, including both
submissions and any private summary or reread.

| Case | Retention correct: current / elision | Repeat correct: current / elision | Summary calls: current / elision | Median seconds: current / elision | Rereads: current / elision |
| --- | --- | --- | --- | --- | --- |
| Bulk | 4/4 / 4/4 | 4/4 / 4/4 | 4 / 0 | 9.44 / 4.59 | 0 / 0 |
| Reread | 4/4 / 4/4 | 4/4 / 4/4 | 4 / 0 | 8.92 / 7.23 | 0 / 4 |
| History only | 4/4 / 0/4 | 4/4 / 0/4 | 4 / 0 | 9.94 / 3.95 | 0 / 0 |

In the history-only arm, all four retention answers and their four repeats
reported `null`; none
invented a code. The stored evidence remained available to the human/session
tree, but was unavailable to the model because this experiment exposes no
history-retrieval tool. Faster incorrect/unknown answers are not a quality-adjusted
performance improvement.

The live run used the initial driver whose hash is recorded as `driver_sha256`.
Independent review identified a latent scoring problem (an absent later answer
could reuse an earlier answer) and missing scripted aggregation. The current
driver fixes both. Every original saved synthetic session was rescored at its
own user-message boundary with the corrected scorer; all 48 submissions have
their own clean final and the original correctness outcomes were unchanged.
The report records the corrected analysis code as `analysis_driver_sha256`,
and preserves the original live timing and token counters. No model calls were
rerun to obtain these repairs. The original hashed driver is retained with the
private evidence.

The reproduction command now directly emits tested aggregates. The retained
measurement is the original run reanalyzed with that same implementation,
removing its private workspace pointer. To reproduce that analysis when the
original private report and synthetic session trees are available:

```sh
uv run python scripts/context_elision_measure.py \
  --analyze-existing /tmp/pipy-context-elision-20261011.json \
  --report /tmp/pipy-elision-reanalyzed.json
```

New public reports omit the private directory pointer. For them, also pass
`--evidence-root /path/to/the/printed/private-directory`. Omitting that option
and a legacy private pointer produces a clear argument error before analysis.

Provider token totals below include both submissions, every ordinary iteration,
and every private summary. Cached input is already included in total input.
"Uncached" subtracts the provider's reported cached subset; it is not a bill.

| Case | Input: current / elision | Cached: current / elision | Uncached input: current / elision | Output: current / elision |
| --- | --- | --- | --- | --- |
| Bulk | 22,140 / 45,835 | 0 / 22,016 | 22,140 / 23,819 | 756 / 138 |
| Reread | 23,505 / 71,505 | 0 / 17,664 | 23,505 / 53,841 | 704 / 256 |
| History only | 22,395 / 46,002 | 0 / 22,016 | 22,395 / 23,986 | 789 / 88 |
| Total | 68,040 / 163,342 | 0 / 61,696 | 68,040 / 101,646 | 2,249 / 482 |

Elision avoided all 12 summary operations, but added four ordinary provider
iterations and four rereads. Total input increased 140%; uncached input increased
49%. Output tokens decreased substantially because current compaction generated
summaries. No dollar-savings conclusion follows from these token classes alone.
In the successful bulk case, median elapsed time fell 51%, while uncached input
rose 8%. In the successful reread case, median elapsed time fell 19%, while
uncached input rose 129%.

The fake-provider integration check separately verifies one summary for current
preparation versus none for elision at this ceiling, with paired identity/prose
preservation and the same durable invariants. It verifies instrumentation, not
live model quality.

## Decision and follow-up

Do not adopt this generic read-output elision policy as a default. These results
support a latency benefit on these fixtures, not a general cost benefit or
adequate retention of transient evidence. Avoided summarization alone was a
misleading efficiency proxy. The result qualifies, rather than reproduces, the
paper's staged-policy finding: different protected context and amortization
choices matter.

A later owner-selected slice could evaluate elision limited to explicitly
disposable/recoverable observations, or cheap elision that retains small factual
extracts. Establish a retrieval route for transient evidence before discarding
it from provider context. Then compare real edit/test tasks over longer sessions
and different output distributions, including tool errors and non-recoverable
results. Preserve current product behavior until that evidence exists.

Validation closed on 2026-10-11. `just check` passed twice: initially 8,191
tests with two skips, then 8,199 with two skips after the scoring/aggregation
repairs. The last counter-completeness and reanalysis-directory repairs passed
105 focused measurement/compaction tests, repository-wide lint/format/type
checks, documentation build and `git diff --check`. Native product code is
unchanged. The final analysis hash matches the driver and all original saved
sessions were rescored successfully without provider calls.

Independent Opus 5.5/medium review used the Claude review-loop harness. The
first review found two Warnings and two Suggestions; the delta review found
one Warning and one Suggestion. All six findings were accepted and fixed,
with additional regression checks. The final delta review returned **CLEAN**,
exit 0, with no omissions, redactions or truncations; all prior findings were
closed. No further review was run after that clean result. No product default,
global setting or theme was changed.

### CI follow-up

The first pushed evaluation commit exposed a Python 3.11 test-double failure:
ordinary assignments to extra fields on a frozen, slotted fake-provider subclass
failed before evaluation execution. The test double now explicitly permits only
its measurement fields to mutate; provider configuration remains frozen. The
live driver, original measurements and conclusions are unchanged.

That CI run also exposed a pre-existing PTY test-helper race: terminal closure
could occur before the child exit status became observable. The helper now waits
within its original deadline before declaring failure, with deterministic EOF,
EIO and timeout regression coverage. Both focused suites pass on Python 3.11
and 3.14. This is a test-only repair, so no product release-note entry applies.

The CI repair passed 25 focused tests on each Python version and the Python 3.11
real-PTY smoke suite. Independent Opus 5.5/medium review reported two Suggestions
and no Critical or Warning findings. Both suggestions were applied: simulated
closure tests use pipes rather than real PTY devices, include timeout coverage,
and the test-only change has no product changelog entry. The advisory review
cycle closed without a further pass.
Full `just check` then passed with 8,203 tests and two skips; the documentation
build and `git diff --check` also passed.
