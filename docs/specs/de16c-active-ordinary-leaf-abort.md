# DE16c — active ordinary strict root-leaf cancellation

## Source status

This private implementation extends immutable DE16b b4ae, not moving Main.
The adopted plan's R2 was a terminal advisory (0C/0W/1S), not CLEAN; Root accepted
it with the mandatory C7 failure-transport test condition. Root accepted focused
QA on immutable `1f507d1933d8248880c29281f86ed00d655002ed` (receipt `a9fee928`):
whole mypy 1155, Ruff/format 1287, Core collection/start/JUnit 166 with 165 PASS
and one existing Memory cold-reopen skip, and Broad 51 PASS. The
SQLite counterpart of the skipped Core case passed. Current34 configuration,
recipe, coverage and strict docs passed on that same source. This documentation
successor holds every executable, test and support byte; these receipts keep
their actual 1f50 source qualification.

The finite C7 cases ran, including escaping run CancelledError after held finally
and the real completion-Future join on the `_step` catch/fail/return-None path.
They are runtime evidence, not an upgrade of the PLAN advisory or an independent
CODE verdict. Broad retained four new active cuts plus eleven earlier cuts and
fourteen real OPENER records. Captured boundary, fsynced callback effects, actual
process exits and raw SQL observations stay distinct: no exhaustive lifecycle,
exactly-once, atomic DB/callback counter or all-platform claim is made. No new
posthoc SQL-table snapshot evidence is claimed.

At this status checkpoint, independent CODE, public Pi comparison, Full and
Main delivery remain open. Prior Core94/Broad42/P1/CODE receipts retain their
original epochs. Focused acceptance alone does not close those later gates or
DE16/P10 and the parent criteria.

## Authority and physical completion

The internal `TaskScheduler.aabort_leaf` preserves the paused/retired pending
path and adds only the exact same scheduler's current ordinary **run-mode**
strict root leaf. Current exact code/version, complete graph, ordinary kind,
state, restart reason, abandonment, Session health and lifecycle checks remain.
No unrelated busy task, synthetic running row or active abort callback receives
this authority. A terminal target returns without writes or a cleanup join.
A phase-host/self-join or Session-line reentrant request refuses before enqueue.

The external coroutine is constructed outside the Session condition; its final
healthy/open/host check and `run_coroutine_threadsafe` enqueue share the condition
that seals close. Unsubmitted coroutines are disposed outside it. Each physical
invocation owns a constructor-created concurrent Future, distinct from logical
`ended`. The host drain or tentative-reservation failure path is its only
completer, after callback/finally unwind, durable handoff and identity-safe slot
retirement. Future callbacks and cooperative signals never run under the guard.
No `Task.cancel` or fabricated completion ends a run.

A real committed false-to-true mark publication schedules the signal on the host.
Speculative transaction staging cannot signal. Runtime transactions already ahead
of the mark keep their FIFO right; a newly committed child can make the later
mark refuse. A winning mark fences subsequent runtime changes, including memos,
children and progress, and suppresses later no-progress/ordinary exception
terminalization. A previously admitted phase may enter after publication, but
its writes still refuse. This is not a wall-clock assertion that no callback
could start late.

## Drain handoff, repeats and failure

After the real run unwinds, the drain retains the identical occupied slot while
an actual Session transaction normalizes a still-running ordinary mark to
pending with its current checkpoint. It preserves mark, counters, memos and
documents, validates the complete graph, and grants no abort execution right.
Logical `ended=True` does not refuse this physical handoff. Pending candidate
selection subsequently rechecks full topology, enablement, cutoff, missing code
and exact version using the unchanged DE16a/b policy.

Same-run repeats include both ended/physical-finally and committed pending-
handoff/adoption windows. They join the identical Future, without another mark,
counter reset, invocation or Future. Pending state by itself grants no exception:
the real same run-mode slot must still be present. Later abort-mode work refuses
this active-run route and is never signalled by it. Pausing after reservation does
not prevent signalling/joining the old run and does not enable its pending abort.
Definition publication retains its existing busy fence.

Each request registers a one-shot `Session.subscribe_close` listener before its
mark transaction, using a concurrent Future for thread-safe close notification.
No caller-loop asyncio Future is set from a foreign thread. Cancellation only
removes the request observer; settlement, mark, run and handoff remain owned.
Before classifying a transported `CancelledError` as caller cancellation, the
request reads guarded authoritative Session failure and raises
`SessionFailed(original)` when present. This includes a genuine run
`CancelledError` escaping after held finally cleanup. The physical Future remains
pending through that cleanup and is completed exceptionally by the same host.

This private policy deliberately gives an already latched authoritative
`Session.failure` priority at the guarded projection snapshot even when genuine
caller cancellation or `asyncio.timeout` coincides. Without that failure, the
observer's `CancelledError` propagates; observer removal does not cancel the
owned run. An outstanding `Task.cancelling()` count alone does not identify the
current exception's origin. Root owns a fresh decision before public SDK/full
P10 adoption. This is not a general public or Pi cancellation-result parity
claim.

Normal `_run` return does not establish health: `_step` can catch a backend
exception, fail the Session and return None. The handoff and request both preserve
first-failure authority. Healthy close can wake the request with
`SessionClosedError` before physical completion; close still joins real cleanup
and retains the backend lease. A sealed healthy owner skips a fresh handoff and
uses the existing stopped-row rewrite after service join. Noncooperative cleanup
can keep that join pending; cancellation is not a lease-release proof.

## Recovery and comparison limits

Recovery of a durably running marked run charges one interruption even before
abort entry; a pending-only cut does not. Cutoff still precedes definition lookup,
missing-only settlement remains DE16b, and version drift holds until exact
restoration. No attempt is added by normal handoff or healthy close.

Literal Pi 4a08 can re-reserve a running marked record; Native has an extra
pending transaction. Preserve that status/cut window and raw storage differences.
Pi public `abortTask` joins `run.done`; it is not an early mark ACK. Neither abort
entry versus outer request-return ordering nor concurrent healthy-close request
outcome parity is promised. Native may return SessionClosedError early while Pi
waits for its run. Paired close evidence must hold Pi's callback before terminal
commit, rather than treating logical task removal as physical completion.

No new public API, canonical profile/phase, persisted field, owned cascade,
generic failFast, restart abandonment, timer or reserved model/tool capability is
added. DE16/P10 and the wider parent remain open.
