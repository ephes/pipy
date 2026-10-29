# B6 acceptance matrix — plan requirements

These are adopted acceptance requirements, not a claim that every family has
executed. The [evidence record](../evidence/de14b6-orphan-failed-migration.md)
records actual core, broad and reference development on their respective source
epochs at 2026-10-29; independent CODE and final acceptance are separate receipts.
Reuse existing Rig, Session tickets, failure/retirement observers, SQL snapshots
and owned-join helpers. Positive authority requires a real observed failure;
synthetic malformed graphs are labelled negative controls.

| Family | Required discriminating witness |
|---|---|
| Warm success | Memory and SQLite: real create/initial baseline, stored older version and actual newer migrator fails once. Exercise separately (a) a native-only actually paused/retired control and (b) actual native retired-enabled success paired with the Pi enabled-quiescent target lane; use the Admission and Pi common rows, not a paused Pi fixture. In each success, settlement migration count remains1 and target phase/abort0; raw outcome migration_failed and one terminal+doc retirement. |
| Missing observation | Same current newer binding with a real migrator before any attempt refuses; missing-migrator inspection may say migration_failed but action refuses, no callback/no write. |
| Version/definition priority | Missing, equal numeric version and older definitions refuse even when an old memo exists; newer matching memo succeeds. Preserve existing numeric ABI, no subtraction/string comparison. Keep these definition/identity cases separate from the relation-isolating witness below. |
| Strictly-newer relation in isolation | **Native-only paused control.** Establish a genuine failure memo and retain the **identical current Binding**. Before any version update, await actual `apause()` acknowledgement and real reservation/invocation retirement; keep the owner paused through the action and assertions, without private flag writes. Then use legitimate `tx.set_task` on the real Session to raise the pending stored version to the captured version, and in a separate case to a still newer value. Verify the same memo/current Binding identity remains and that no reservation or invocation started between update and action. Take the complete stored state/revision/sequence baseline after that committed update, then require the exact unsupported refusal and full no-write equality. No memo injection; the identity predicate stays true so a removed `captured.version > stored.version` check is exposed. This witness is separate from the enabled common lane. |
| Identity preservation | Same-source reinstall and unrelated publication retain matching authority after real quiescence. Remove then readd same source while failure memo retains binding does not call migrator again. |
| Replacement/overwrite | Distinct same-version/newer declaration refuses before its own failure; genuinely fail B to overwrite A, restore A and refuse until a new A observation. No cache injection/fingerprint; release displaced references outside guard. |
| Cold pending and terminal | Reopen unchanged pending SQLite with same declaration: absent new-owner memo refuses; actual new-owner failure then success. Reopen terminal with no/new/equal/older definition returns terminal without callback/write. |
| Retained memo semantics | Existing allowed task update does not manufacture a record fingerprint. Eligibility uses current numeric ordering and existing task-ID/current-binding memo exactly; no callback re-run merely to revalidate checkpoint/input. |
| FIFO publication | Actual queued publication before action changes the chosen binding and may refuse; action before publication uses prior matching binding. Observe real Session tickets, never guessed method labels or timers. |
| Admission | B6-specific success distinguishes native-only paused and actual retired-enabled common admission. The unchanged shared `_admit` busy/final-gate boundaries reuse the three named B5 tests below; these are not B6-entry-point-specific evidence. A held migration on the line queues the action and does not prove immediate busy. |
| Lifecycle and validation | Existing exact ID/reentry/off-loop/failure/closed/not-open precedence and reserved pi.* including terminal; unknown ID; generic terminal before graph/cache lookup. Reuse existing discriminating cuts, no protocol Cartesian expansion. |
| Strict graph | Existing B4/B5 malformed/pages/cursors/endpoint/cycle/owner/terminal-child/background-child/incoming-waiter refusal coverage retained; new action-specific representative late-page child and incoming waiter negatives plus unrelated positives. |
| Atomic retirement | Loaded/unloaded and >256 real task docs retire with terminal; preserve unrelated/conversation/session docs, entries, headers and counters. SQLite all9tables/current-only revisions and Memory incarnation/sequence readbacks; no rewindable task-doc fiction. |
| Precommit rollback | Actual storage precommit failure keeps the full stored state, task/doc revisions and callback baseline unchanged. It fails the owner; legitimate shutdown may clear its owner-local memo. The action does not manually clear the memo, but post-failure memo identity retention is neither observed nor guaranteed. |
| Commit/ACK loss | Real committed terminal then ACK loss: caller error, failed owner closed, cold terminal winner replay once/no second revision; distinguish precommit rollback. |
| Cancellation/lifetime | Sync and async result; accepted worker cancellation, close and loop teardown join actual commit worker and lease before genuine second opener succeeds. Retain existing finite normal actor routes. |
| Retained authority | Existing genuine C1/C2 reserved refusal/nonce/receipt/catalog saved-winner controls remain green and source-closed; no fabricated authority frames. |
| Pi common case | Actual Full Harness resumed to a genuinely observed cached failure, then **enabled-quiescent target settlement on the actual Session line**, paired with native actual retired-enabled admission; cold terminal replay remains separate. Pi has no pause API. Retain full raw callback/TaskRecord/SQL/doc evidence; no private flags, graph-idle or blocked-completion assumption. Native paused controls are not this common case. Uncached/missing-migrator Pi abort is not normalized into native behavior. |
| Scope/quality | Closed SDK/DTO/common consumer and AST inventories, ordinary whole mypy/Ruff/format after all overlays, source-closed manifests and integrated affected consumers before CODE. Final gates remain separate actual runs. |

Positive cross-lane setup must establish the failure once before action; neither full graph idle nor task completion is assumed for parked pending work. Existing actual retirement witness/current guard is used natively; Pi real line boundary is labelled as such. Cases, mutants and runtime counts come from actual collection and execution receipts; none are inferred from this matrix.

## Explicit retained boundary evidence

The following existing tests cover unchanged shared `_admit` boundaries through
B4/B5 entry points, not B6-specific refusal invocations:

- `tests/durable/test_orphan_task_too_old.py::test_real_drain_refuses_even_when_the_session_line_is_available`
- `tests/durable/test_orphan_task_too_old.py::test_real_service_admission_during_scan_wins_the_final_gate`
- `tests/durable/test_orphan_task_too_old.py::test_actual_unrelated_invocation_blocks_control_without_target_mutation`
- `tests/durable/test_orphan_missing_definition.py::test_failed_and_closed_valid_id_preserve_existing_lifecycle_authority`

These retained broad cuts were **not included in the714-case early integrated
subset**. They remain requirements for the actual current final focused selection
and ordinary full suite, with their outcomes recorded there. This reuse does not
claim that B6-specific busy or failure-before-closed cases executed. The B6
precommit test checks stored nine-table/revision rollback and callback counts;
owner failure and normal shutdown prevent a promised post-failure memo identity.
