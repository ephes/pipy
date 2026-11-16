# Local test gate duration profile

2026-11-15, fresh SSH clone of `main` at `e31dc2d2`, macOS, Python 3.15.2,
Pytest 9.0.3, xdist 3.8.0, 16 logical CPUs, default eight workers.
Baseline runs are sequential repeats; the first has cold rewrite caches.

**These are timings of failing runs, not green delivery evidence.** Both
baselines ran all 21,534 cases: 21,486 passed, 21 skipped, 27 failed, plus
679 teardown errors. The sandbox denies process inspection (`ps`), affecting
the MCP leak guard and zombie/process cleanup tests. Other baseline failures
include two docs-preview refresh timeouts, the legacy parity score (47/49),
and a local Pi system-prompt reference mismatch. None is skipped or waived here.

## Verification outside the sandbox (2026-11-16)

Fresh clone, same Mac (16 CPUs, eight workers), with other agents' builds and
test runs sharing the machine (load average 10–14), so single timings vary by
about ±20 s. Runs alternated between `main` and the branch:

| Run | Wall (`/usr/bin/time -p`) | Result |
| --- | ---: | --- |
| `main` `e31dc2d2`, run 1 | 438.81 s | 1 failed |
| branch `c9f4179b` (worksteal), run 1 | 464.24 s | 1 failed |
| `main`, run 2 | 421.99 s | 2 failed |
| branch (worksteal), run 2 | 472.88 s | 1 failed |
| branch with `--dist=loadfile` | 425.10 s | 1 failed |

With `ps` allowed, none of the 27 failures or ~700 teardown errors above
recur. Every run failed only
`tests/test_native_system_prompt_pi_pinned.py::test_fixture_is_current_with_pi`
(the local Pi checkout no longer matches the pinned prompts; it fails on
`main` too). `main` run 2 also hit a one-off `PermissionError` from
`os.killpg` in the teardown of
`tests/test_command_sandbox.py::test_cancellation_escalates_after_parent_exit_when_descendant_holds_pipes`.
The docs-preview tests passed (they were sandbox timeouts).

Work stealing was consistently slower on the shared machine, so full runs keep
`--dist=loadfile`; the other changes below are kept. The profiled `loadfile`
run spent 3,317 summed test seconds (call 2,766 s, setup 425 s, teardown
127 s) with a longest/mean worker ratio of 1.005: the wall time is the total
work divided by eight, not one worker's tail.

## Sandbox measurements (2026-11-15)

Everything from here to "Remaining costs" was measured in a sandbox that
denied `ps`, on the earlier branch state that used `--dist=worksteal` for full
runs. "Final" below means that rejected work-stealing state, not what was
merged; the merged state keeps `loadfile`.

| Run | Wall | Result |
| --- | ---: | --- |
| Baseline 1, loadfile | 436.40 s | 27 failed, 679 errors |
| Baseline 2, loadfile | 428.77 s | 27 failed, 679 errors |
| Scheduling only, worksteal | 378.32 s | same 27 failed, 711 errors |
| Final full gate, all changes | 391.81 s | same 27 failed, 713 errors |
| Actual branch `just check` (escalates to full) | 489.07 s | same 27 failed, 717 errors |

Commands: `/usr/bin/time -p just test-full --durations=60`, repeated once,
and then the same command with `--dist=worksteal`. The final full run uses
the branch's then-default `worksteal` scheduling, without a `--dist` override. A temporary read-only Pytest
plugin recorded all setup/call/teardown reports, per-worker collection/elapsed
clocks and `pytest_fixture_setup` costs. The profile tables aggregate every
report, including durations below the terminal top-60 cutoff. Wall includes
Just/uv startup. Fixture times exclude their dependencies and teardown; file
and test totals include all three phases. Parallel totals are not wall savings.

`worksteal` preserves the full item list but can run a module fixture on more
than one worker. This explains the additional blocked module-teardown errors.
It was not adopted; see the verification above.

## Changes and coverage

- Work stealing saved 50.45–58.08 s wall in the sandboxed scheduling-only
  experiment but was slower outside the sandbox; it is not used.
- Collection resolves a test path once per file. cProfile measured 21,534
  `_rel` calls / 1.338 s cumulative in the old collection hook. A synthetic
  101-case/two-file regression requires two resolutions and unchanged markers.
- Two orphan-admission tests observe entry to the actual `_drain`; the
  migration test observes the real pause retirement barrier. Their old guard
  conditions did not notify at admission, so eight cases waited five seconds
  each despite reaching the intended state. Events retain the original five-
  second failure bound and locked state assertions; the real continuations run.
- Seven task-runtime JSON mutants share a module capture of all three real
  backends per worker. Each case gets a deep copy. Only the captured JSON is mutated;
  producer execution does not depend on the mutant name. No case is redundant
  or removed, and no crash/recovery test or full-tier matrix is shortened.
- Durable closure pins and their parent hashes are refreshed for the changed
  tests/support and Unreleased note. Product/runtime/reference bytes, pinned Pi
  release, schema, case lists and validation predicates remain unchanged.

A GC probe found only 1.133 s in 2,939 collections during a selected 30.31 s
12-case run after full collection. No GC freezing/disabling was adopted.

## Baseline top 30 tests

Total setup + call + teardown seconds, ranked by baseline 1.

| Test | Run 1 | Run 2 |
| --- | ---: | ---: |
| `tests/durable/test_session_guard_comparison.py::test_native_negative_controls_through_actual_paired_recipes[registration]` | 77.589 | 77.711 |
| `tests/test_docs_build.py::test_preview_mirrors_changes_reloads_config_and_cleans_up_child[15]` | 61.252 | 61.109 |
| `tests/test_docs_build.py::test_preview_mirrors_changes_reloads_config_and_cleans_up_child[2]` | 61.149 | 61.159 |
| `tests/durable/test_session_commit_comparison.py::test_valid_native_mismatch_still_runs_real_mutant_controls_and_exits1` | 27.784 | 28.128 |
| `tests/durable/test_session_checkpoints_comparison.py::test_actual_native_three_backends_match_pinned_session_with_closed_differences` | 21.087 | 18.866 |
| `tests/durable/test_documents_comparison.py::test_actual_cli_private_predecessor_import_and_environment_guards[clean]` | 18.052 | 22.157 |
| `tests/durable/test_session_checkpoints_comparison.py::test_comparator_real_cli_returns_0_1_2_and_reaps_children` | 12.334 | 12.642 |
| `tests/durable/test_interrupted_comparison.py::test_actual_sdk_crash_cold_cli_interrupted_next_answer_and_reopen` | 11.731 | 11.873 |
| `tests/durable/test_sqlite_comparison.py::test_actual_native_sqlite_reopen_and_closed_observed_inventory` | 10.599 | 9.161 |
| `tests/durable/test_sqlite_comparison.py::test_native_adapter_open_count_includes_helper_commits` | 10.437 | 11.772 |
| `tests/durable/test_session_checkpoints_comparison.py::test_reference_projection_cannot_hide_arbitrary_values_or_unobserved_fields` | 9.906 | 9.681 |
| `tests/durable/test_sqlite_comparison.py::test_real_native_cursor_loss_is_causal_mismatch_with_bound_report` | 9.415 | 7.428 |
| `tests/durable/test_details_comparison.py::test_actual_cli_next_history_reopen_and_absent_null` | 9.175 | 9.789 |
| `tests/test_parity_probe_trust.py::test_legacy_parity_score_opts_into_trusted_workspace_fixtures` | 8.915 | 9.667 |
| `tests/durable/test_tool_comparison.py::test_actual_producer_and_fresh_pure_import_have_identical_native_maps` | 8.677 | 12.227 |
| `tests/durable/test_draft_tracker_comparison.py::test_private_source_mutants_defeated_by_designated_tests[if _equal(before, after):-if False:-test_restore_cancels_but_structural_identity_survives]` | 8.141 | 8.113 |
| `tests/durable/test_tool_reporting_presence.py::test_public_retained_presence_ack_saved_request_and_auth_free_cli[deep1500]` | 8.128 | 7.995 |
| `tests/test_parity_lessons.py::test_real_repo_ledger_is_valid` | 8.038 | 9.466 |
| `tests/durable/test_tool_details_presence.py::test_public_sqlite_tool_producer_admission_and_reopen_do_not_reissue[deep1500]` | 7.850 | 7.826 |
| `tests/durable/test_tool_comparison.py::test_actual_replay_intersection_attempts_differ_from_applications` | 6.928 | 13.624 |
| `tests/durable/test_reporting_comparison.py::test_actual_cli_reporting_failed_child_next_answer_and_reopen` | 6.422 | 6.659 |
| `tests/durable/test_documents_comparison.py::test_release_15_cache_read_counts_mask_only_the_declared_pair[A1-conversation]` | 6.406 | 6.778 |
| `tests/durable/test_delta_ops_comparison.py::test_private_source_mutants_defeated_by_designated_tests[root: Any = value.thaw()-root: Any = {}-test_owned_atomicity_late_invalid_and_detached_outputs]` | 6.220 | 6.221 |
| `tests/durable/test_session_deltas.py::test_operation_limit_root_replacement_remains_delta[sync-4096-sqlite]` | 6.187 | 5.340 |
| `tests/test_god_file_decomposition_final_audit.py::test_named_locks_have_one_alias_resistant_explicit_construction` | 5.703 | 5.559 |
| `tests/durable/test_session_checkpoints_comparison.py::test_valid_semantic_mutations_reach_the_comparison[kind]` | 5.660 | 5.068 |
| `tests/durable/test_draft_tracker_comparison.py::test_compare_cli_zero_one_two` | 5.638 | 5.296 |
| `tests/durable/test_task_migration.py::test_real_reconciliation_commits_around_failed_generic_candidate[sqlite-True]` | 5.620 | 5.708 |
| `tests/durable/test_task_migration.py::test_real_reconciliation_commits_around_failed_generic_candidate[sqlite-False]` | 5.578 | 5.708 |
| `tests/test_native_coding_session_terminal_pty.py::test_pty_long_input_soft_wraps_typing_paste_and_cursor_insert[100-40-ghostty]` | 5.565 | 4.092 |

## Baseline top 15 files

| File | Run 1 | Run 2 |
| --- | ---: | ---: |
| `tests/durable/test_session_guard_comparison.py` | 128.218 | 128.636 |
| `tests/test_docs_build.py` | 126.916 | 126.717 |
| `tests/durable/test_sqlite_storage.py` | 99.211 | 93.863 |
| `tests/durable/test_session_deltas.py` | 97.464 | 88.879 |
| `tests/durable/test_tool_interrupted_sdk.py` | 96.951 | 106.426 |
| `tests/durable/test_session_commit_comparison.py` | 96.386 | 92.079 |
| `tests/durable/test_session_checkpoints_comparison.py` | 86.820 | 84.375 |
| `tests/durable/test_draft_tracker_comparison.py` | 82.323 | 78.847 |
| `tests/durable/test_sqlite_comparison.py` | 69.461 | 64.493 |
| `tests/test_durable_loop_integration.py` | 67.519 | 73.800 |
| `tests/durable/test_memory_comparison.py` | 61.336 | 57.203 |
| `tests/durable/test_documents_comparison.py` | 57.897 | 61.673 |
| `tests/durable/test_session_commit.py` | 50.890 | 43.670 |
| `tests/durable/test_restart_limit_sdk.py` | 49.637 | 51.843 |
| `tests/test_durable_prototype.py` | 43.059 | 44.454 |

## Baseline top fixture setup costs

Totals across all workers; dependencies are measured separately.

| Fixture (scope) | Run 1 | Run 2 |
| --- | ---: | ---: |
| `tests::_isolated_chrome_theme (function)` | 50.749 | 55.127 |
| `tests::_isolated_user_home (function)` | 40.314 | 40.655 |
| `tests/durable/test_session_deltas.py::rig (function)` | 28.067 | 26.757 |
| `::tmp_path (function)` | 17.567 | 19.288 |
| `tests/durable/test_session_checkpoints_comparison.py::actual (module)` | 17.533 | 15.947 |
| `tests/durable/test_session_documents.py::rig (function)` | 14.476 | 13.684 |
| `tests/durable/test_interrupted_comparison.py::native_run (module)` | 11.729 | 11.871 |
| `tests/durable/test_session_checkpoints.py::rig (function)` | 10.673 | 9.714 |
| `tests/durable/test_sqlite_comparison.py::paired (module)` | 10.512 | 9.078 |
| `tests/durable/test_details_comparison.py::native_run (module)` | 9.171 | 9.782 |
| `tests/durable/test_tool_comparison.py::native_run (module)` | 6.918 | 13.615 |
| `tests/durable/test_reporting_comparison.py::native_run (module)` | 6.417 | 6.644 |
| `tests/durable/test_headless_comparison.py::native_run (module)` | 5.219 | 6.343 |
| `tests/durable/test_documents_comparison.py::paired (module)` | 5.000 | 5.682 |
| `tests/durable/test_task_creation.py::rig (function)` | 4.259 | 3.568 |

## Collection and worker balance

Active time is summed test setup/call/teardown time per worker, including
blocked waits. It excludes collection and controller overhead.

| Run / worker | Collection | Active |
| --- | ---: | ---: |
| baseline1 / gw0 | 19.379 | 378.018 |
| baseline1 / gw1 | 19.121 | 408.907 |
| baseline1 / gw2 | 19.308 | 379.589 |
| baseline1 / gw3 | 19.130 | 378.885 |
| baseline1 / gw4 | 19.138 | 380.850 |
| baseline1 / gw5 | 18.965 | 379.226 |
| baseline1 / gw6 | 18.874 | 380.041 |
| baseline1 / gw7 | 19.068 | 383.817 |
| baseline1: longest / mean | — | 1.0658× |
| baseline2 / gw0 | 6.595 | 384.891 |
| baseline2 / gw1 | 6.486 | 386.685 |
| baseline2 / gw2 | 6.332 | 388.613 |
| baseline2 / gw3 | 6.192 | 388.551 |
| baseline2 / gw4 | 6.401 | 387.504 |
| baseline2 / gw5 | 6.292 | 416.157 |
| baseline2 / gw6 | 6.166 | 386.392 |
| baseline2 / gw7 | 6.279 | 385.730 |
| baseline2: longest / mean | — | 1.0655× |
| worksteal / gw0 | 6.059 | 367.531 |
| worksteal / gw1 | 6.234 | 364.084 |
| worksteal / gw2 | 6.252 | 361.273 |
| worksteal / gw3 | 6.267 | 360.654 |
| worksteal / gw4 | 6.237 | 358.998 |
| worksteal / gw5 | 6.303 | 358.125 |
| worksteal / gw6 | 6.264 | 357.545 |
| worksteal / gw7 | 6.321 | 358.139 |
| worksteal: longest / mean | — | 1.0187× |
| final / gw0 | 3.990 | 382.704 |
| final / gw1 | 3.872 | 382.500 |
| final / gw2 | 3.743 | 375.579 |
| final / gw3 | 4.035 | 376.211 |
| final / gw4 | 3.961 | 373.316 |
| final / gw5 | 3.792 | 372.293 |
| final / gw6 | 3.808 | 369.379 |
| final / gw7 | 3.639 | 372.813 |
| final: longest / mean | — | 1.0189× |

## Sandbox focused savings and remaining files (work-stealing state)

Seconds below are summed setup/call/teardown costs in the eight-worker full
profiles, not additive wall savings. Worker contention varies; the actual
branch gate took longer than either profiled work-stealing run. The full target
is missed, and the scheduling-only 12–13 % result is not a guaranteed speedup.

| Group | Baseline 2 | Final |
| --- | ---: | ---: |
| Too-old orphan admission (two backends) | 10.100 | 0.061 |
| Missing-definition orphan admission (two backends) | 10.116 | 0.157 |
| Migration pause/reconciliation (four cases) | 22.123 | 1.849 |
| Entire task-runtime comparison file | 12.167 | 2.205 |

The changed five test files were rerun five times with `pytest -n 2`:
220 test bodies passed each time (Pytest times 15.40 / 14.56 / 15.48 /
14.42 / 14.86 s). Each run still had 8–10 denied process-guard teardowns.
Ruff, repository format checking, Mypy (1,167 files) and `just docs-build`
passed. No cross-family review was run, as instructed.

That work-stealing profile contained 21,536 cases. The merged state has
21,535: the baseline's 21,534 plus the new collection-cost regression; the
bounded-worker routing test is unchanged. All original product and
crash/recovery cases remain. The sandbox's 27 failed call IDs equalled the
baseline set exactly; no new call failure was introduced.

| Work-stealing top 15 files | Total seconds |
| --- | ---: |
| `tests/durable/test_session_guard_comparison.py` | 128.069 |
| `tests/test_docs_build.py` | 126.733 |
| `tests/durable/test_tool_interrupted_sdk.py` | 105.662 |
| `tests/durable/test_session_commit_comparison.py` | 93.341 |
| `tests/durable/test_session_deltas.py` | 82.287 |
| `tests/durable/test_sqlite_storage.py` | 82.003 |
| `tests/durable/test_draft_tracker_comparison.py` | 74.668 |
| `tests/durable/test_session_checkpoints_comparison.py` | 73.568 |
| `tests/test_durable_loop_integration.py` | 70.566 |
| `tests/durable/test_sqlite_comparison.py` | 61.936 |
| `tests/durable/test_documents_comparison.py` | 56.466 |
| `tests/durable/test_memory_comparison.py` | 56.121 |
| `tests/durable/test_restart_limit_sdk.py` | 44.870 |
| `tests/durable/test_session_commit.py` | 44.385 |
| `tests/durable/test_tool_adjudication.py` | 42.299 |

## Remaining costs and gate status

The sandboxed work-stealing run (391.81 s, 9–10 % below its baselines) did not
hold up outside the sandbox. The merged `loadfile` state measured 425.10 s
against 421.99–438.81 s for `main` on the same shared machine: the event waits
and the shared capture remove about 50 summed test seconds, roughly 6 s of wall
time with eight workers, which is within the run-to-run noise. The 300 s target
is missed.
The 77 s registration negative control deliberately prevents progress in real
guard scenarios and hits bounded non-progress waits. The two 61 s docs-preview
timeouts were sandbox failures; outside the sandbox those tests pass in about 2 s.
Real crash/cold-recovery cuts, child interpreter startup, fsynced SQLite commits
and separate CLI comparator controls dominate the remaining profile; replacing
those boundaries with fakes would change what is covered. These timings alone
do not prove that every remaining cost is irreducible.

Sandbox, work-stealing state: clean-checkout `just check` took 55.29 s with a
cold Mypy cache and 2.77 s warm; both had
13 passed test bodies and 11 blocked process-guard teardowns. A plugin change
escalates the actual branch gate to full by design; the narrow timing must not
be presented as validation of this branch. After committing the changes,
`PIPY_TEST_BASE=HEAD /usr/bin/time -p just check` took 2.37 s: 13 test bodies
passed, 1,439 were deselected, and the same 11 process-guard teardowns failed.
The override models the no-change gate after landing; that branch diff was
checked separately in the 489.07 s sandboxed full escalation. For the merged
`loadfile` state, the evidence is the outside-sandbox verification above
(425.10 s, and a `just check` escalated to full that took 499.05 s with only
the environmental Pi-reference failure).

The sandbox blocked the process inspection commands `ps -axww -o pid=,command=`
and `ps -o stat= -p <pid>`. The verification above reran the full gate where
they are allowed: the docs-preview and score failures did not recur, and the
Pi-reference failure is environmental and also fails on `main`.
