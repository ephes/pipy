# MCP parity acceptance note (2026-10-13)

Status: **implemented on `feat/mcp-parity`, not merged.** Of the 292
checklist rows of the
[MCP parity plan](../specs/2026-10-12-mcp-parity-plan.md), 173 are
closed with a test and 119 are closed with a recorded limit or
deviation; no row is open. The official MCP
client conformance suite gives the same result for pipy as for Pi. Live use
with a real server and a real model (plan section 6.5) has **not** been
recorded; see [What this note does not show](#what-this-note-does-not-show).

This note is the closeout of slice S40. It was written by the agents that
implemented the slice, from the tests and documents in the repository: a
first pass on 2026-10-13 and a second one on 2026-10-14 that checked the
first. It is evidence for the owner's final judgement, not that judgement.

The 119 rows that are closed with a limit count as closed only if the
judgement accepts the deviation or limit each one names (plan section 3).
The owner accepted the DEV rows that plan section 7, Q11 and Q12, name
(2026-10-15).

Updated on 2026-10-15 (INT3), when the goal-check tracks L and N were merged:
five rows were added (TLS-20, TLS-21, EXP-13, TSR-12, OAU-37), SLH-13 and the
open part of MGR-09 were built (N2, N3), and the rows the slices G1 to G9,
L1 to L4 and N1 to N4 changed name their new tests and limits.

Updated again on 2026-10-15 after the triage of the second goal check: ten
accepted findings became the new rows DEV-69 to DEV-78, which set OAU-02, OAU-03, OAU-11,
OAU-15 and STD-01 to `closed, with limits`, and the owner accepted the DEV
rows plan section 7, Q11, names. The fix slices of that triage (R1 to R6)
change the rows they fix when they land.

Updated in INT4 (2026-10-15), when R2 (extension tool registration) and R5
(structured tool results in JSON/RPC events) were merged: EXT-01, EXT-02,
EXT-04, EXT-11, EXT-15, TLS-10 and CMD-24 name their new tests and changed
limits, and the new DEV-79 sets EXT-01 to `closed, with limits`.

Updated in R7 (2026-10-15): another session in the same run no longer
keeps the tools, tool renderer resolvers and MCP servers the extensions
registered after loading, so EXT-15 loses the resolver added again in each
new session, and the coordinator accepted DEV-79 and the `duration_ms` part
of DEV-48 under the triage decision (plan section 7, Q11).

Updated again on 2026-10-15 after the triage of the third goal check:
six accepted findings became the new rows DEV-80 to DEV-85, which set
CFG-23, CFG-24, HTP-01 and SES-01 to `closed, with limits`; DEV-54 now also
names OAU-26 and OAU-31 (a callback path is compared as written), which
sets them to `closed, with limits` too. The owner accepted the DEV rows plan
section 7, Q12, names. The fix slices of that triage (T1 to T6) change the
rows they fix when they land.

Updated in INT5 (2026-10-15), when T3, T4 and T6 were merged: TLS-20,
EXP-12, CFG-25, OAU-36, EXP-06, EXT-15, CMD-26 and STD-05 name their
new tests, and EXT-15 loses the wrapped `edit` and `bash` rows from its
limits (DEV-45). No row changes its state.

## Reference

- Pi: `~/src/pi-mono` at `a447ba11f3e7760fcff91e924bda8c20033953ea`
  (2026-10-12), as the plan records.
- pipy: branch `feat/mcp-parity`, slices S01 to S40 and X1. (X2 came after
  this note and added DEV-63, and G6 added DEV-64; neither changes a row.
  The goal-check slices G1 to G9, L1 to L4 and N1 to N4 came after it too;
  INT3 folded their rows, tests and DEV-65 to DEV-68 in.) The merge to
  `main` is an owner approval (plan section 7, Q5).
- Checks at the time of writing: `just check` passes (see the slice's
  commit), `just docs-build` reports no issues.
- The result is also in the plan, section 3.2.

## How the audit was made

1. Every row of plan section 2 was read against the test files of the
   branch. A row is listed with tests whose names say what the row says.
   Many tests name their row in a comment, and those were the starting
   point for 188 rows. The other 99 rows were matched by reading the test
   names of the module that implements them and, where a name left doubt,
   the test's body. The audit did not check, row by row, that every exact
   text a row quotes is asserted.
2. The limits were taken from the notes each slice left ("rows not closed",
   "limits on how they are closed") and from the "Different from Pi"
   sections of the user documents, and kept only where they still hold at
   the end of the campaign.
3. `tests/test_mcp_parity_audit.py` keeps the tables honest in one respect:
   every row of the plan appears exactly once, and every test named here
   exists. It does not prove that a test covers its row; that is the
   reading described in step 1.

4. The second pass checked the first by program and by sample. By program:
   107 rows quote a text of more than one word, 174 texts in all. 127 of
   them are found in a test the row's line names. The other 47 were looked
   at one by one: most are notation (a shape such as `{name, description}`,
   an option's spelling, a path); the user-visible texts among them are
   asserted in a test that builds the text from parts or stands in another
   file; two are Pi's texts that pipy does not print, the extension error
   of SLH-07 (DEV-39) and the refusal of SLH-13 (open). The count of
   mirrored Pi cases below was taken again from the Pi files at the
   reference commit, with the same result. By sample: clauses of rows that
   state several things (status codes, limits, option spellings, the
   untrimmed prompt text) were looked up in the tests the row names.
5. When plan section 3.1 was written, every row a recorded difference
   changes was set to `closed, with limits` with the DEV row in its line,
   also where the first pass had it as `closed`: 55 rows changed that way.
   No row became open.

The audit did not rerun each test against a mutated implementation, and it
did not compare behaviour with a running Pi. What Pi does was taken from the
Pi sources the plan cites.

## Not closed

No row is open. The two rows the audit left open were built afterwards:
SLH-13 (commands and expansion in queued RPC input) in N2, with the limits
of DEV-65, which R3 removed, and the hyperlink part of MGR-09 in N3.

## Deviations

Plan section 3 lists DEV-01 to DEV-14, which were proposed before or while
the slices were built. They hold as written.

The slices recorded further differences from Pi. Plan section 3.1 now lists
them as DEV-15 to DEV-85, each with the rows it changes, its kind (narrower,
other form, stricter) and where it is recorded. Every row named there names
the DEV row in the "Limits and deviations" column of the tables below and
is `closed, with limits`. The ones a user is most likely to meet:

- **Notices appear while the session waits for input, not when they
  arise** (DEV-15; SES-04, EXT-10): one that arises during a run is shown
  after it, and the `mcp_servers_change` hooks still run before the next
  prompt.
- **A refused `/reload` keeps the running MCP servers** (DEV-16; SES-20);
  Pi restarts them in every case.
- **`auth.provider` differs in details** (DEV-52; OAU-36): no ambient cloud
  credentials.
- **No proxy support** (DEV-82; HTP-01): HTTP servers and the OAuth requests
  connect directly, without `HTTP_PROXY`/`HTTPS_PROXY` or an `httpProxy`
  setting.
- **Nested calls have no session-wide sequential mode** (DEV-48;
  EXT-11; a tool's `execution_mode` is honoured since H3).
- **Image processing needs the optional `images` extra** (DEV-68; TLS-20),
  and images are drawn in the terminal only with the Kitty or iTerm2
  protocol, on Kitty another type than PNG only with that extra (DEV-19; TLS-11).

The owner accepted the DEV rows that plan section 7, Q11, names
(2026-10-15), among them DEV-69 to DEV-79; DEV-79 and the `duration_ms`
part of DEV-48 were added the same day under the same triage decision.
Q12 (2026-10-15, the third goal check) accepts the DEV rows it names,
among them DEV-80 to DEV-85, DEV-59 and DEV-66. The
others of DEV-15 to DEV-68, except the closed DEV-39, DEV-42 and DEV-65, are
still proposals for the final judgement. DEV-06, DEV-07 and DEV-09 are owner decisions since 2026-10-14
(plan section 7, Q1 and Q7).

## Conformance suite

`just mcp-conformance` runs `@modelcontextprotocol/conformance@0.2.0-alpha.11`
(client scenarios, protocol versions `2025-03-26`, `2025-06-18` and
`2025-11-25`) against a driver that uses `McpServerConnection` and
`sign_in_mcp_server`. Run on 2026-10-13 with Node 26.11.1: 26 scenario runs,
and pipy's committed baseline equals Pi's `test/mcp-conformance/baseline.json`
check for check, with `pipy-client` where Pi has `pi-client`. The failing
checks are Pi's three known failures: elicitation (6 checks),
`auth/basic-cimd` (`cimd-client-id-used`) and the simulated user of
`auth/scope-retry-limit`. The bar of plan section 6.4, no failing check
that passes in Pi's baseline, is met. No product code was changed to reach
it. The recipe is manual (DEV-12); `tests/test_mcp_conformance_recipe.py`
pins the baseline's failing checks in `just check`.

## Mirrored Pi tests

Plan section 6.3 names the Pi test files to mirror. The table counts the
`it(...)` cases of each file at the reference commit and how many have a
pipy test that carries the case's name in a `Pi:` comment or as its own
name. "In scope" leaves out the cases the plan excludes: the JavaScript-only
case of `streamable-http.test.ts`, the codemode cases that are not about
reaching tools (DEV-04, DEV-13; the five store cases count since slice G4
ported `store()`/`load()`; the cases of what G5 ported, the options line,
structured `bash`, images from `read` and the output limits, are not yet
recounted), and the three declaration cases
outside the two groups the plan names. The counts are taken from the files.
`agent-session-mcp.test.ts` has 48 cases there (44 plain and four
parametrized ones); the plan said 44 and is corrected.

| Pi test file | Cases | In scope | Mirrored | Not mirrored |
| --- | --- | --- | --- | --- |
| `mcp/test/client.test.ts` | 12 | 12 | 12 | none |
| `mcp/test/content.test.ts` | 2 | 2 | 2 | none |
| `mcp/test/stdio.test.ts` | 1 | 1 | 1 | none |
| `mcp/test/streamable-http.test.ts` | 15 | 14 | 14 | none |
| `mcp/test/oauth.test.ts` | 12 | 12 | 12 | none |
| `coding-agent/test/mcp-extension.test.ts` | 27 | 27 | 27 | none |
| `coding-agent/test/mcp-command.test.ts` | 8 | 8 | 8 | none |
| `coding-agent/test/mcp-manager-view.test.ts` | 2 | 2 | 2 | none |
| `coding-agent/test/mcp-oauth-refresh.test.ts` | 11 | 11 | 11 | none |
| `coding-agent/test/mcp-oauth-store.test.ts` | 3 | 3 | 3 | none (the two legacy-key cases since L2) |
| `coding-agent/test/tool-search.test.ts` | 9 | 9 | 9 | none |
| `coding-agent/test/suite/agent-session-mcp.test.ts` | 48 | 48 | 48 | none |
| `coding-agent/test/suite/agent-session-mcp-oauth.test.ts` | 9 | 9 | 9 | none |
| `coding-agent/test/suite/agent-session-tool-orchestration.test.ts` | 3 | 3 | 3 | none |
| `coding-agent/test/suite/agent-session-codemode.test.ts` | 24 | 11 | 11 | none |
| `codemode/test/declarations.test.ts` | 17 | 14 | 14 | none |
| Total | 203 | 186 | 186 | |

The counts are of test names, not of assertions. A mirror may run at another
level than Pi's case: Pi's session cases use its faux provider and an
in-memory server, and pipy's mirrors of them are partly session tests with
the stdio fake and partly tests at the manager or the capability value.

Five cases had a pipy test without the name comment. This slice added the
comment after reading both tests, and for one case a test:

| Pi case | pipy test | Note |
| --- | --- | --- |
| `resolves the OAuth client secret lazily` | `test_native_mcp_connection_oauth.py`: `test_a_client_secret_that_cannot_be_resolved_fails_the_refresh_only` | pipy's test also shows the refresh failing and working once the secret resolves. |
| `lists servers with their state, tools, and errors, and fails while anything is wrong` | `test_native_mcp_shell_cli.py`: `test_lists_servers_with_state_tools_and_errors_and_fails_while_anything_is_wrong` | |
| `routes tools whose names differ only in - and _ (reverse: %s)` | `test_native_mcp_manager.py`: `test_calls_reach_tools_whose_names_differ_only_in_dash_and_underscore` | New in this slice. At the manager, with direct calls; Pi's case finds the two tools from a codemode script. |
| `withdraws and restores %s MCP tools the server changes` | `test_native_mcp_manager.py`: `test_a_changed_list_adds_withdraws_and_restores_tools` | At the manager; one server with a `direct` and a `codemode` tool instead of one run per exposure. |
| `keeps structured content that tool_result handlers replace along with the content` | `test_native_extension_tool_result_hooks.py`: `test_tool_result_hooks_see_and_decide_the_structured_content` | At the hook dispatcher; `test_native_agent_nested_calls.py` covers what the script receives. |

## Row audit

One table per section of the plan's checklist. "Slice" is the slice the plan
assigns the row to. "State" is `closed`, `closed, with limits`, `part open`
or `open`. "Tests" names tests in `tests/`; a row may have more tests than
the ones named. "Limits and deviations" says where the row is closed in
another form than Pi's, or less far; `DEV-nn` refers to plan section 3.

### Configuration (CFG)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| CFG-01 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_paths`, `test_merges_user_and_trusted_project_servers_and_validates`; `test_native_mcp_session.py`: `test_a_trusted_project_adds_its_servers` | `projectConfig` is Pi's field; pipy's loader reports the project path only for a trusted project. |
| CFG-02 | S14 | closed | `test_native_mcp_config.py`: `test_missing_files_are_not_an_error`, `test_invalid_json_is_reported_with_the_path_and_the_other_file_still_loads`, `test_a_file_of_the_wrong_shape_is_reported_and_the_other_still_loads`; `test_native_mcp_config.py`: `test_bytes_that_are_not_utf8_are_read_as_replacement_characters` (L3) |  |
| CFG-03 | S14 | closed | `test_native_mcp_config.py`: `test_an_invalid_entry_is_skipped_and_the_rest_load` |  |
| CFG-04 | S14 | closed | `test_native_mcp_config.py`: `test_a_project_entry_that_defines_a_server_replaces_the_whole_entry` |  |
| CFG-05 | S14 | closed | `test_native_mcp_config.py`: `test_an_override_merges_only_its_three_keys` |  |
| CFG-06 | S14 | closed | `test_native_mcp_config.py`: `test_project_entries_override_enabled_and_exposure`, `test_an_override_is_validated_after_the_merge` |  |
| CFG-07 | S14 | closed | `test_native_mcp_config.py`: `test_rejects_server_names_that_differ_only_in_dash_and_underscore` |  |
| CFG-08 | S14 | closed | `test_native_mcp_config.py`: `test_validates_provider_auth_and_accepts_it_only_at_user_level`, `test_project_auth_is_rejected_whenever_the_entry_has_a_url_key` |  |
| CFG-09 | S14 | closed | `test_native_mcp_config.py`: `test_validates_exposure_and_reads_auto_enable_codemode`, `test_project_auto_enable_codemode_overrides_the_user_level_value` |  |
| CFG-10 | S14 | closed | `test_native_mcp_config.py`: `test_invalid_server_names`, `test_invalid_entries_give_their_error_text` |  |
| CFG-11 | S14 | closed | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text`, `test_valid_entries_are_returned_as_a_copy` |  |
| CFG-12 | S14 | closed | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text` |  |
| CFG-13 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text` | DEV-54. |
| CFG-14 | S14 | closed | `test_native_mcp_config.py`: `test_validation_resolves_aliases_in_a_deep_copy`, `test_tool_exposure_defaults_to_codemode`, `test_invalid_entries_give_their_error_text` |  |
| CFG-15 | S14 | closed | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text` |  |
| CFG-16 | S14 | closed | `test_native_mcp_config.py`: `test_resolves_per_tool_exposure_from_exact_names_then_patterns` |  |
| CFG-17 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text`, `test_a_whole_number_callback_port_becomes_an_int` | DEV-54. |
| CFG-18 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_validates_the_oauth_callback_url_scope_and_client_name`, `test_is_loopback_redirect_uri` | DEV-54. |
| CFG-19 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text`, `test_validates_the_oauth_callback_url_scope_and_client_name` | DEV-06: `clientMetadataBaseUrl` is required with `cimd`. |
| CFG-20 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_invalid_entries_give_their_error_text` | DEV-54. |
| CFG-21 | S14 | closed | `test_native_mcp_config.py`: `test_validates_provider_auth_and_accepts_it_only_at_user_level` |  |
| CFG-22 | S14 | closed | `test_native_mcp_config.py`: `test_disabled_servers_stay_in_the_loaded_list` |  |
| CFG-23 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_update_keeps_other_content_and_four_space_indentation`, `test_update_writes_defaults_in_an_override_entry`, `test_update_with_override_creates_a_missing_entry`, `test_update_of_a_server_the_file_does_not_define` | A file holding a number JSON cannot write back (`1e999`) is not rewritten; the edit fails. DEV-81. |
| CFG-24 | S14 | closed, with limits | `test_native_mcp_config.py`: `test_add_creates_the_file_and_its_directory`, `test_add_replaces_an_entry_and_reports_it`, `test_remove_reports_whether_the_entry_existed` | A file holding a number JSON cannot write back (`1e999`) is not rewritten; the edit fails. DEV-81. |
| CFG-25 | S15 | closed | `test_native_mcp_config_values.py`: `test_templates_replace_environment_references`, `test_a_command_value_is_its_trimmed_output`, `test_headers_are_resolved_and_named_in_errors`; `test_native_mcp_connection.py`: `test_env_values_are_resolved_and_an_unresolved_one_names_server_and_key`; `test_native_auth_store.py`: `test_resolve_config_value_command_runs_and_strips`, `test_command_unicode_success_and_nonzero`, `test_command_invalid_utf8_is_replaced` (r3 F3) |  |
| CFG-26 | S15 | closed | `test_native_mcp_connection.py`: `test_home_is_expanded_in_command_arguments_and_cwd`, `test_a_relative_cwd_resolves_against_the_session_directory` |  |

### Protocol client (CLT)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| CLT-01 | S11 | closed | `test_native_mcp_client.py`: `test_initializes_the_connection_before_exposing_server_information`, `test_initialize_announces_roots_title_and_configured_capabilities`, `test_client_is_not_usable_before_the_handshake_completes` |  |
| CLT-02 | S11 | closed | `test_native_mcp_client.py`: `test_accepts_servers_that_answer_with_an_older_protocol_version`, `test_rejects_an_unsupported_protocol_version_and_closes_the_transport` |  |
| CLT-03 | S11 | closed | `test_native_mcp_client.py`: `test_rejects_an_invalid_initialize_result` |  |
| CLT-04 | S11 | closed | `test_native_mcp_client.py`: `test_initializes_the_connection_before_exposing_server_information`, `test_rejects_an_unsupported_protocol_version_and_closes_the_transport` |  |
| CLT-05 | S11 | closed | `test_native_mcp_client.py`: `test_requests_carry_increasing_ids_and_return_the_result`, `test_the_client_default_timeout_applies_and_can_be_overridden`, `test_cancels_aborted_and_timed_out_requests` |  |
| CLT-06 | S11 | closed | `test_native_mcp_client.py`: `test_cancels_aborted_and_timed_out_requests`, `test_does_not_send_cancelled_for_a_timed_out_initialize` |  |
| CLT-07 | S11 | closed | `test_native_mcp_client.py`: `test_renews_the_timeout_on_progress`, `test_progress_for_unknown_tokens_and_malformed_progress_is_ignored` |  |
| CLT-08 | S11 | closed | `test_native_mcp_client.py`: `test_surfaces_json_rpc_errors`, `test_a_response_for_an_unknown_request_is_reported_and_changes_nothing` |  |
| CLT-09 | S11 | closed | `test_native_mcp_client.py`: `test_answers_ping_and_rejects_unknown_methods`, `test_answers_roots_list_and_dispatches_notifications`, `test_request_handlers_answer_with_their_result_or_error`, `test_a_server_cancellation_aborts_the_matching_handler` |  |
| CLT-10 | S11 | closed | `test_native_mcp_client.py`: `test_notifications_reach_every_listener_and_a_raising_one_is_isolated` |  |
| CLT-11 | S11 | closed | `test_native_mcp_client.py`: `test_reports_transport_errors_without_failing_pending_requests`, `test_notifies_close_listeners_once_when_the_transport_drops`, `test_close_rejects_pending_requests_notifies_once_and_closes_the_transport` |  |
| CLT-12 | S12 | closed | `test_native_mcp_client.py`: `test_paginates_tools_and_preserves_protocol_tool_definitions`, `test_a_null_cursor_ends_pagination`, `test_a_repeated_cursor_fails_the_list`, `test_a_list_with_too_many_pages_fails` |  |
| CLT-13 | S12 | closed | `test_native_mcp_client.py`: `test_rejects_an_invalid_tools_page`, `test_rejects_an_invalid_resource_or_template_entry`, `test_resource_templates_page_and_fall_back_to_the_uri_template` |  |
| CLT-14 | S12 | closed | `test_native_mcp_client.py`: `test_defaults_missing_tool_result_content_to_an_empty_list`, `test_rejects_an_invalid_tool_result`, `test_rejects_an_invalid_read_resource_result` |  |
| CLT-15 | S12 | closed | `test_native_mcp_protocol.py`: `test_passes_text_and_images_through_and_replaces_other_blocks_with_text`, `test_falls_back_to_the_structured_content_as_json_when_there_are_no_blocks`, `test_unknown_and_malformed_blocks_become_a_placeholder`, `test_an_embedded_image_blob_needs_no_uri` |  |
| CLT-16 | S15 | closed, with limits | `test_native_mcp_connection.py`: `test_the_client_announces_pipy_and_the_session_directory_as_its_one_root` | DEV-05: the client is named `pipy`. |

### Stdio transport (STD)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| STD-01 | S13 | closed, with limits | `test_native_mcp_stdio.py`: `test_server_runs_with_the_parent_environment_plus_env_in_cwd_and_own_group`, `test_missing_executable_fails_the_client_connection` | No `stderr: "inherit"`; stderr is always piped. DEV-71. |
| STD-02 | S13 | closed | `test_native_mcp_stdio.py`: `test_sends_newline_delimited_json`, `test_strips_carriage_returns_and_skips_blank_lines`, `test_invalid_lines_are_reported_without_closing` |  |
| STD-03 | S13 | closed | `test_native_mcp_stdio.py`: `test_a_message_over_16_mib_is_dropped_with_an_error`, `test_oversize_messages_are_dropped_and_the_stream_continues` |  |
| STD-04 | S13 | closed | `test_native_mcp_stdio.py`: `test_keeps_the_last_64_kib_of_stderr_readable_after_the_server_failed` |  |
| STD-05 | S13 | closed, with limits | `test_native_mcp_stdio.py`: `test_close_sends_sigterm_after_the_grace_period`, `test_close_kills_a_server_that_ignores_shutdown_including_its_children`, `test_close_terminates_children_that_outlive_a_server_that_exited`, `test_close_kills_a_child_that_holds_the_server_output_and_ignores_sigterm` (added in G1); `test_native_mcp_stdio.py`: `test_an_exited_child_is_waited_for_without_reaping_it` (r3 P1) | DEV-09: POSIX only. G1: `close()` stops waiting after its SIGKILL for a holder of the pipes that left the group, where Pi waits. |
| STD-06 | S13 | closed | `test_native_mcp_stdio.py`: `test_servers_still_alive_at_interpreter_exit_are_sent_sigterm` |  |
| STD-07 | S13 | closed | `test_native_mcp_stdio.py`: `test_server_exit_closes_the_transport_and_reports_a_partial_message`, `test_start_is_allowed_once_and_not_after_close` |  |

### Streamable HTTP transport (HTP)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| HTP-01 | S24 | closed, with limits | `test_native_mcp_http.py`: `test_every_message_is_a_post_with_accept_json_body_and_configured_headers` | No proxy support. DEV-82. |
| HTP-02 | S24 | closed | `test_native_mcp_http.py`: `test_handles_json_and_sse_responses_with_session_and_protocol_headers`, `test_the_auth_provider_token_replaces_a_configured_authorization_header` |  |
| HTP-03 | S24 | closed | `test_native_mcp_http.py`: `test_a_json_array_delivers_every_message`, `test_an_event_stream_delivers_every_message_and_skips_what_is_none`; `test_native_mcp_http.py`: `test_a_json_answer_is_not_held_to_the_message_limit` (L1) |  |
| HTP-04 | S24 | closed | `test_native_mcp_http.py`: `test_rejects_a_request_the_server_accepts_without_a_response`, `test_notifications_and_responses_ignore_the_response_body`, `test_rejects_another_content_type` |  |
| HTP-05 | S24 | closed | `test_native_mcp_http.py`: `test_classifies_authentication_failures`, `test_classifies_an_expired_established_session`, `test_an_http_error_shows_500_characters_of_at_most_8_kib_of_body` |  |
| HTP-06 | S30 | closed | `test_native_mcp_http.py`: `test_hands_401_and_insufficient_scope_403_to_the_auth_provider`, `test_a_request_is_handed_to_the_auth_provider_only_once`, `test_another_403_is_an_error_and_is_not_handed_over` |  |
| HTP-07 | S24 | closed | `test_native_mcp_sse.py`: `test_parses_chunked_crlf_events_comments_ids_and_multiline_data`, `test_lf_line_ends_and_event_names`, `test_on_retry_gets_decimal_values_only`; `test_native_mcp_http.py`: `test_an_event_over_the_message_limit_fails_its_request` |  |
| HTP-08 | S25 | closed | `test_native_mcp_http.py`: `test_opens_the_get_stream_after_initialization_and_sends_last_event_id_only_when_resuming`, `test_reconnects_the_get_stream_after_it_drops`, `test_the_reconnect_delay_doubles_from_one_second_to_thirty`, `test_the_get_stream_gives_up_after_five_failures_in_a_row` |  |
| HTP-09 | S25 | closed | `test_native_mcp_http.py`: `test_resumes_a_response_stream_the_server_closed_before_answering`, `test_fails_only_the_request_whose_sse_stream_breaks`, `test_fails_a_request_whose_response_stream_ends_without_an_answer` |  |
| HTP-10 | S25 | closed | `test_native_mcp_http.py`: `test_the_get_stream_is_opened_again_after_a_transient_status`, `test_the_get_stream_is_opened_again_after_a_network_failure`, `test_the_get_stream_ends_with_an_error_that_is_not_transient`, `test_a_response_stream_broken_by_a_protocol_error_is_not_resumed` |  |
| HTP-11 | S24 | closed | `test_native_mcp_http.py`: `test_closes_the_session_with_the_last_requests_token`, `test_close_waits_one_second_for_the_delete_and_ignores_its_failure`, `test_closing_ends_every_io_thread_and_fails_what_is_in_flight`; `test_native_mcp_http_fetch.py`: `test_closing_ends_a_tls_handshake_in_progress`, `test_a_request_timeout_ends_a_tls_handshake_in_progress` (L1) |  |

### Server connection (CON)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| CON-01 | S15 | closed | `test_native_mcp_connection.py`: `test_a_new_connection_is_connecting_and_connects_on_the_first_call`, `test_marks_a_dropped_connection_and_reconnects_on_the_next_call`, `test_calls_after_close_fail_as_shut_down` |  |
| CON-02 | S15 | closed | `test_native_mcp_connection.py`: `test_requests_time_out_after_the_configured_seconds_default_sixty`, `test_progress_notifications_reset_the_timeout` |  |
| CON-03 | S15 | closed | `test_native_mcp_connection.py`: `test_connects_to_servers_without_the_tools_capability_without_listing_tools`, `test_tools_are_listed_when_the_capability_is_declared` |  |
| CON-04 | S15 | closed | `test_native_mcp_connection.py`: `test_instructions_are_kept_trimmed_and_empty_ones_count_as_none` |  |
| CON-05 | S15 | closed, with limits | `test_native_mcp_connection.py`: `test_a_tool_list_change_lists_the_tools_again`, `test_a_failed_refresh_is_recorded_and_keeps_the_tools`; `test_native_mcp_connection_resources.py`: `test_a_slow_resource_listing_does_not_delay_a_tool_refresh` (L1) | DEV-56. |
| CON-06 | S15 | closed, with limits | `test_native_mcp_connection.py`: `test_marks_a_dropped_connection_and_reconnects_on_the_next_call`, `test_a_dropped_stdio_server_is_reported_with_its_stderr_and_restarted`, `test_a_dropped_server_reports_only_the_end_of_a_long_stderr` | DEV-56. |
| CON-07 | S15 | closed | `test_native_mcp_connection.py`: `test_a_failed_connect_is_recorded_and_the_next_call_tries_again`, `test_a_stdio_server_that_fails_to_start_is_reported_with_its_stderr` |  |
| CON-08 | S15 | closed | `test_native_mcp_connection.py`: `test_concurrent_callers_share_one_connection_attempt` |  |
| CON-09 | S15 | closed | `test_native_mcp_connection.py`: `test_closes_a_connection_that_is_still_initializing_before_close_returns`, `test_calls_after_close_fail_as_shut_down`; `test_native_mcp_connection_http.py`: `test_stops_waiting_to_retry_a_connection_when_closed`; `test_native_mcp_http_fetch.py`: `test_a_tcp_connect_in_progress_ends_when_it_is_woken_or_times_out` (L1) |  |
| CON-10 | S26 | closed, with limits | `test_native_mcp_connection_http.py`: `test_retries_http_connections_that_fail_with_a_transient_error`, `test_a_connect_is_tried_three_times_with_the_two_delays`, `test_only_network_errors_and_some_statuses_are_tried_again`, `test_stdio_connects_are_not_retried` | Network failures are tested with a refused local connection; no test uses HTTPS. DEV-56. |
| CON-11 | S26 | closed, with limits | `test_native_mcp_connection_http.py`: `test_retries_resource_reads_but_not_tool_calls_after_a_transient_http_error`, `test_a_resource_request_waits_before_it_is_made_again` | DEV-17. |
| CON-12 | S26 | closed, with limits | `test_native_mcp_connection_http.py`: `test_starts_a_new_session_and_retries_once_when_the_session_expired`, `test_the_old_session_stays_open_for_its_other_calls`, `test_a_session_that_expires_again_fails_the_call` | DEV-56. |
| CON-13 | S30 | closed | `test_native_mcp_connection_oauth.py`: `test_an_http_server_uses_oauth_unless_it_has_an_authorization_header_or_auth` |  |
| CON-14 | S30, S33 | closed, with limits | `test_native_mcp_connection_oauth.py`: `test_a_server_nobody_signed_in_to_needs_a_sign_in`, `test_asks_oauth_servers_that_keep_rejecting_requests_for_a_new_sign_in`; `test_native_mcp_provider_auth.py`: `test_the_sign_in_text_names_the_provider_login_for_a_server_with_auth` | DEV-55. |
| CON-15 | S15 | closed, with limits | `test_native_mcp_log.py`: `test_a_line_has_time_server_level_logger_and_message`, `test_message_forms`, `test_write_failures_are_ignored`; `test_native_mcp_connection.py`: `test_appends_server_log_messages_to_the_log_file` | DEV-56. |
| CON-16 | S15 | closed | `test_native_mcp_log.py`: `test_a_file_over_the_limit_is_renamed_before_the_next_line` |  |

### Tool adaptation and results (TLS)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| TLS-01 | S16 | closed | `test_native_mcp_tools.py`: `test_creates_provider_safe_tool_names` |  |
| TLS-02 | S16 | closed | `test_native_mcp_tools.py`: `test_creates_provider_safe_tool_names`, `test_names_count_characters_as_pi_counts_utf16_units` |  |
| TLS-03 | S16 | closed, with limits | `test_native_mcp_tools.py`: `test_colliding_tools_all_get_the_hash_suffix_in_any_order`, `test_a_refresh_that_adds_a_colliding_tool_renames_both`, `test_a_name_once_owned_by_another_tool_is_not_reused`; `test_native_mcp_manager.py`: `test_calls_reach_tools_whose_names_differ_only_in_dash_and_underscore` | DEV-36. |
| TLS-04 | S16 | closed | `test_native_mcp_tools.py`: `test_description_falls_back_to_the_title_then_to_a_generic_text` |  |
| TLS-05 | S16 | closed, with limits | `test_native_mcp_tools.py`: `test_input_schema_gets_type_and_properties_when_missing` | DEV-36. DEV-74. |
| TLS-06 | S16 | closed | `test_native_mcp_tools.py`: `test_declares_the_call_tool_result_scripts_receive`, `test_output_schema_is_the_result_with_the_tools_schema_inside` |  |
| TLS-07 | S16 | closed | `test_native_mcp_tools.py`: `test_only_the_boolean_hints_become_annotations` |  |
| TLS-08 | S16 | closed | `test_native_mcp_tools.py`: `test_namespace_carries_alias_description_and_instructions` |  |
| TLS-09 | S16 | closed | `test_native_mcp_tools.py`: `test_mcp_exposure_maps_to_tool_exposure` |  |
| TLS-10 | S16, R5 | closed, with limits | `test_native_mcp_tools.py`: `test_a_call_sends_arguments_timeout_and_cancellation`, `test_progress_notifications_become_live_updates`, `test_the_executor_validates_arguments_against_the_servers_schema`; `test_native_agent_event_adapters.py`: `test_automation_projection_emits_pi_tool_result_objects` (R5) | DEV-10: arguments are checked with pipy's own validator for a named keyword set. In the terminal, progress is appended to the call's live output line by line; JSON/RPC events and `on_update` get Pi's replacing partial result with `{server, tool}` details. DEV-18. |
| TLS-11 | S16, S39 | closed, with limits | `test_native_mcp_tools.py`: `test_converts_results_passing_the_call_tool_result_to_scripts`, `test_images_are_image_blocks_of_a_result_in_the_servers_order`; `test_native_mcp_session_images.py`: `test_mcp_images_reach_every_adapter_family_and_survive_a_resume`; `test_native_tool_row_inline_images.py`: `test_a_drawn_image_leaves_the_rows_text`, `test_without_a_protocol_or_with_show_images_off_the_line_stays` (N4), `test_kitty_draws_another_type_converted_to_png`, `test_kitty_shows_the_line_for_an_image_pillow_cannot_convert` (R4) | Like Pi, pipy does not check a server's image; an image over the size limits is scaled down (TLS-20). An image block without data or type becomes the text `[image <mime> without data omitted]`. The terminal draws the image where it can (Kitty, iTerm2; on Kitty another type than PNG only with Pillow), else shows a line that names it. DEV-19, DEV-21. |
| TLS-12 | S16, S23 | closed, with limits | `test_native_mcp_tools.py`: `test_resource_link_parts_appear_only_when_present`, `test_resource_links_name_the_read_tool_when_resources_are_reachable` | DEV-60. |
| TLS-13 | S16 | closed | `test_native_mcp_tools.py`: `test_a_blob_with_a_text_mime_type_is_decoded_as_text`, `test_a_binary_blob_is_saved_with_the_extension_of_its_uri`, `test_a_binary_blob_that_cannot_be_saved_says_why` |  |
| TLS-14 | S16 | closed | `test_native_mcp_tools.py`: `test_an_error_result_is_an_error_that_keeps_the_result_for_scripts`, `test_is_error_follows_javascript_truthiness` |  |
| TLS-15 | S16 | closed, with limits | `test_native_mcp_tools.py`: `test_cuts_the_middle_of_model_facing_text_over_20kb`, `test_cuts_multi_byte_text_only_at_character_boundaries`, `test_text_at_the_limit_is_kept_and_one_byte_more_is_cut` | DEV-60. |
| TLS-16 | S16 | closed | `test_native_mcp_tools.py`: `test_the_spill_file_is_new_and_readable_only_by_the_user`, `test_a_failed_save_is_reported_and_publishes_no_path` |  |
| TLS-17 | S16 | closed | `test_native_mcp_tools.py`: `test_cuts_the_middle_of_model_facing_text_over_20kb`, `test_converts_results_passing_the_call_tool_result_to_scripts` |  |
| TLS-18 | S16 | closed | `test_native_mcp_tools.py`: `test_converts_results_passing_the_call_tool_result_to_scripts`, `test_a_long_result_is_cut_for_the_model_and_whole_for_scripts` |  |
| TLS-19 | S17 | closed | `test_native_mcp_manager.py`: `test_a_call_to_a_tool_the_server_withdrew_is_no_longer_available`, `test_a_call_to_a_hidden_tool_is_no_longer_available`, `test_a_call_after_the_session_closed_says_the_server_is_disabled`, `test_a_call_resolves_its_server_when_it_runs` |  |
| TLS-20 | G7 | closed, with limits | `test_native_tool_result_image_normalize.py`: `test_an_oversized_image_is_resized_and_followed_by_its_note`, `test_another_type_is_converted_to_png_with_a_hint`, `test_an_image_that_does_not_decode_is_kept`, `test_without_auto_resize_an_oversized_image_is_kept`, `test_the_loop_normalizes_every_result_after_the_hooks`, `test_a_session_sends_the_resized_tool_result_image`, `test_a_rows_inline_images_are_the_normalized_ones`, `test_the_read_tool_follows_the_auto_resize_setting` (r3 D2); `test_native_tools_read.py`: `test_read_tool_resizes_a_large_image_with_pillow`, `test_read_tool_converts_a_bmp_to_png_with_pillow`, `test_read_tool_keeps_the_size_when_auto_resize_is_off`, `test_read_tool_omits_an_image_pillow_cannot_decode`, `test_without_pillow_auto_resize_off_attaches_a_fitting_type_as_it_is` (r3 D2) | Pillow from the optional `images` extra in place of Photon; without it nothing is resized. `read` processes image files the same way (r3 D2). DEV-68. DEV-75, DEV-76. |
| TLS-21 | G7, R4 | closed | `test_native_tool_result_image_normalize.py`: `test_blocked_images_become_one_text_per_run`, `test_a_session_with_blocked_images_sends_the_text_instead`, `test_blocked_images_also_keep_the_users_attachments_back`, `test_blocking_user_images_marks_the_latest_user_message_or_the_prompt` | The `@image` attachments of the user's turn are replaced as well (R4). |

### Session integration (SES)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| SES-01 | S17 | closed, with limits | `test_native_mcp_manager.py`: `test_start_connects_enabled_servers_in_the_background`, `test_servers_register_in_configuration_order`; `test_native_mcp_manager_registered.py`: `test_servers_registered_before_the_start_connect_with_it`, `test_the_mcp_json_server_wins_over_a_registered_one`; `test_native_mcp_session_switch.py`: `test_another_session_reads_mcp_json_again`, `test_a_product_session_transition_loads_the_servers_again` (N1) | A replaced session keeps the loaded extensions and starts from what they registered while loading; Pi loads them again. DEV-84. |
| SES-02 | S17 | closed, with limits | `test_native_mcp_manager.py`: `test_tools_register_with_the_exposure_the_config_resolves` | DEV-36. |
| SES-03 | S17 | closed | `test_native_mcp_manager.py`: `test_a_changed_list_adds_withdraws_and_restores_tools` |  |
| SES-04 | S19 | closed, with limits | `test_native_mcp_manager.py`: `test_startup_problems_are_reported_once_in_one_notice`, `test_nothing_is_reported_when_nothing_needs_attention`; `test_native_mcp_session.py`: `test_startup_problems_are_reported_once`; `test_native_mcp_idle_notices.py`: `test_the_editor_shows_the_startup_report_while_it_waits`, `test_an_rpc_client_gets_the_startup_report_without_a_command` (N1) | Shown while the session waits for input (the editor, the modal key loops, an RPC `notify`), not at the moment it arises (docs/mcp.md "Startup report"). DEV-15. |
| SES-05 | S18 | closed, with limits | `test_native_mcp_readiness.py`: `test_the_first_prompt_waits_for_servers_with_direct_tools`, `test_the_first_prompt_waits_only_up_to_the_startup_limit`, `test_only_the_first_prompt_waits`, `test_the_first_prompt_reports_servers_that_are_still_connecting`; `test_native_mcp_session.py`: `test_the_first_prompt_waits_for_a_server_with_direct_tools` | Pipy's wait can be interrupted with Escape; Pi's cannot. DEV-17. |
| SES-06 | S18 | closed | `test_native_mcp_readiness.py`: `test_the_first_prompt_does_not_wait_for_servers_without_direct_tools` |  |
| SES-07 | S18 | closed, with limits | `test_native_mcp_readiness.py`: `test_reaches_server_matches_the_namespace_inside_a_word`, `test_an_extension_tool_waits_for_the_reach_it_declares`, `test_a_tool_call_runs_once_its_servers_settled` | DEV-34. |
| SES-08 | S18 | closed | `test_native_mcp_readiness.py`: `test_a_script_reaches_the_servers_its_source_names`, `test_a_script_that_uses_discovery_waits_for_every_server`, `test_a_script_that_names_no_server_waits_for_none`; `test_native_mcp_session.py`: `test_a_script_waits_for_the_server_it_names` |  |
| SES-09 | S18 | closed | `test_native_mcp_readiness.py`: `test_tool_search_and_the_resource_tools_wait_for_every_server`, `test_an_mcp_tool_waits_for_its_own_server_only`; `test_native_mcp_manager.py`: `test_a_wait_covers_a_reconnect_in_progress` |  |
| SES-10 | S18 | closed, with limits | `test_native_mcp_readiness.py`: `test_a_cancel_event_ends_a_wait`, `test_a_wait_covers_an_attempt_that_was_queued_while_waiting`; `test_native_mcp_session.py`: `test_an_abort_ends_the_wait_of_a_tool_call`; `test_native_mcp_readiness_pty.py`: `test_escape_ends_a_tool_calls_wait_and_cancels_the_turn` | DEV-17. |
| SES-11 | S19 | closed, with limits | `test_native_mcp_discovery.py`: `test_codemode_exposure_registers_and_activates_codemode`, `test_deferred_exposure_activates_tool_search_only`, `test_does_not_activate_codemode_when_auto_enable_codemode_is_false`; `test_native_mcp_session.py`: `test_codemode_is_activated_from_the_config` | DEV-33. |
| SES-12 | S19 | closed | `test_native_mcp_session.py`: `test_tool_search_reaches_codemode_tools_without_codemode`, `test_codemode_reaches_deferred_tools_without_tool_search` |  |
| SES-13 | S19 | closed, with limits | `test_native_mcp_discovery.py`: `test_the_warning_texts_are_pis`, `test_warns_when_deferred_tools_have_neither_tool_search_nor_codemode`, `test_warns_once_in_a_session` | DEV-33. |
| SES-14 | S21 | closed, with limits | `test_native_mcp_manager.py`: `test_disabling_closes_the_connection_and_takes_the_tools_away`, `test_disabling_closes_a_server_that_is_still_connecting`, `test_enabling_waits_until_the_previous_connection_has_closed` | DEV-37. |
| SES-15 | S21 | closed, with limits | `test_native_mcp_manager.py`: `test_an_exposure_change_registers_the_tools_again`, `test_tools_that_are_no_longer_direct_leave_a_selection` | DEV-37. |
| SES-16 | S21 | closed | `test_native_mcp_manager.py`: `test_a_change_is_saved_to_the_override_file_when_there_is_one`, `test_session_changes_are_written_to_the_mcp_json_files`, `test_a_server_an_extension_registered_changes_for_the_session_only`, `test_a_failed_save_changes_nothing` |  |
| SES-17 | S17 | closed | `test_native_mcp_manager.py`: `test_close_closes_every_connection_and_publishes_nothing_afterwards`, `test_close_while_a_server_process_is_starting_leaves_no_process`; `test_native_mcp_session.py`: `test_session_end_closes_a_server_that_is_still_connecting`; `test_native_mcp_manager_oauth.py`: `test_closing_the_manager_cancels_a_running_sign_in`; `test_native_mcp_session_switch.py`: `test_replacing_the_session_replaces_the_servers` (N1) |  |
| SES-18 | S31 | closed | `test_native_mcp_session_http.py`: `test_uses_credentials_from_pipy_mcp_login_on_the_next_turn`; `test_native_mcp_manager_oauth.py`: `test_the_turn_start_reconnects_a_server_signed_in_to_elsewhere` |  |
| SES-19 | S17 | closed | `test_native_mcp_session.py`: `test_hooks_block_and_change_direct_mcp_calls`, `test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model`; `test_native_mcp_session_resources.py`: `test_lists_and_reads_resources_with_the_resource_tools` |  |
| SES-20 | S22, N1 | closed, with limits | `test_native_mcp_reload.py`: `test_reload_reads_mcp_json_again`, `test_an_accepted_reload_replaces_the_servers`, `test_a_refused_reload_keeps_the_servers`; `test_native_mcp_session_switch.py`: `test_another_session_reads_mcp_json_again`, `test_a_tool_tool_search_loaded_is_not_declared_in_the_new_session`, `test_a_session_only_change_is_gone_in_the_new_session`, `test_a_registered_server_nothing_connects_is_reported_again` (N1); `test_native_session_replacement.py`: `test_a_replacement_shuts_the_session_down_and_starts_the_next`, `test_a_new_session_starts_from_the_configured_tools`; `test_native_tool_capabilities_mcp.py`: `test_another_session_starts_from_the_configured_selection`, `test_another_session_does_not_keep_a_discovered_codemode` (R3) | A reload that keeps the previous extensions keeps the running servers (docs/mcp.md "Reloading"). DEV-16. |

### `mcp_servers` prompt section (SEC)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| SEC-01 | S20 | closed | `test_native_mcp_session_section.py`: `test_lists_a_server_before_it_connects_and_appends_its_summary_later`, `test_a_disabled_server_and_mcp_turned_off_give_no_section` |  |
| SEC-02 | S20 | closed | `test_native_mcp_servers_section.py`: `test_the_first_line_names_only_the_ways_the_listed_servers_use` |  |
| SEC-03 | S20 | closed | `test_native_mcp_servers_section.py`: `test_lists_servers_with_their_reach_and_the_first_line_of_their_description`, `test_reach_is_codemode_when_any_configured_exposure_is_codemode`, `test_servers_are_sorted_by_name_like_locale_compare` |  |
| SEC-04 | S20 | closed, with limits | `test_native_mcp_servers_section.py`: `test_the_summary_prefers_the_description_and_is_one_trimmed_line`; `test_native_mcp_session_section.py`: `test_lists_servers_by_the_first_line_of_their_instructions` | DEV-28. |
| SEC-05 | S20 | closed, with limits | `test_native_mcp_servers_section.py`: `test_shortens_descriptions_to_fit_the_size_limit`, `test_leaves_out_the_last_servers_when_their_names_alone_do_not_fit`, `test_one_server_left_out_is_counted_in_the_singular` | DEV-01: the closing line names `search_tools()`. |
| SEC-06 | S20 | closed, with limits | `test_native_mcp_session_section.py`: `test_lists_a_server_before_it_connects_and_appends_its_summary_later`, `test_waits_for_servers_with_direct_tools_before_listing_the_servers` | Rendered once per run; a queued follow-up or steering message inside a run does not render it again. DEV-35. |
| SEC-07 | S20 | closed | `test_native_mcp_session_section.py`: `test_leaves_servers_with_only_direct_tools_out_of_the_servers_section`; `test_native_mcp_servers_section.py`: `test_the_manager_lists_a_server_before_and_after_it_connects` |  |

### Exposure semantics and tool selection (EXP)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| EXP-01 | S01, S03 | closed | `test_native_tool_capabilities.py`: `test_exposure_decides_the_declared_and_the_callable_set`; `test_native_tool_metadata.py`: `test_the_exposures_are_pis_five` |  |
| EXP-02 | S03 | closed | `test_native_tool_capabilities.py`: `test_exposure_decides_the_declared_and_the_callable_set`, `test_reload_activates_only_declarable_new_extension_tools` |  |
| EXP-03 | S03 | closed | `test_native_codemode_extension_tools.py`: `test_direct_model_call_to_a_codemode_only_tool_is_an_unknown_tool` |  |
| EXP-04 | S03 | closed | `test_native_tool_capabilities.py`: `test_callable_tools_are_the_definitions_of_the_callable_names_in_order`, `test_codemode_tools_stay_callable_when_a_transcript_restores_the_active_set`, `test_inactive_direct_tools_are_not_callable` |  |
| EXP-05 | S17 | closed | `test_native_mcp_session.py`: `test_a_direct_mcp_tool_is_declared_and_called_like_a_builtin`; `test_native_tool_capabilities_mcp.py`: `test_direct_mcp_tool_is_declared_and_callable_and_others_only_callable` |  |
| EXP-06 | S10 | closed | `test_native_tool_filter_patterns.py`: `test_keeps_mcp_tools_when_tools_names_no_mcp_tool`, `test_an_unnamed_direct_mcp_tool_stays_registered_but_is_not_declared`, `test_does_not_declare_unnamed_mcp_tools_restored_from_the_transcript`, `test_lets_tool_search_declare_unnamed_mcp_tools_when_tools_names_it`; `test_harness_native_cli.py`: `test_tool_selection_flags_combine_as_in_pi`, `test_tool_filter_flags_parse_pi_aliases` (G8); `test_native_tool_capabilities_mcp.py`: `test_a_selection_drops_an_mcp_tool_the_allowlist_does_not_activate` (r3 U2) |  |
| EXP-07 | S10 | closed | `test_native_tool_filter_patterns.py`: `test_filters_mcp_tools_by_the_mcp_entries_of_tools`, `test_an_mcp_entry_keeps_only_the_tools_of_the_servers_it_names` |  |
| EXP-08 | S10 | closed | `test_native_tool_filter_patterns.py`: `test_allow_and_exclude_entries_may_be_patterns`, `test_modifier_entries_take_exact_names_only` |  |
| EXP-09 | S10 | closed | `test_native_tool_filter_patterns.py`: `test_removes_mcp_tools_matching_exclude_tools_patterns`, `test_excluding_the_mcp_prefix_removes_every_server_tool`, `test_removes_mcp_tools_with_no_tools`; `test_native_tool_capabilities_mcp.py`: `test_an_allowlist_after_no_tools_declares_tool_search`, `test_no_builtin_tools_keeps_builtins_registered_and_inactive`, `test_modifiers_after_no_builtin_tools_start_from_nothing` (G8) |  |
| EXP-10 | S23 | closed | `test_native_tool_filter_patterns.py`: `test_server_tools_and_resource_tools_count_as_mcp_tools`; `test_native_mcp_manager_resources.py`: `test_an_allowlist_with_mcp_entries_filters_the_resource_tools_too`, `test_the_resource_tools_are_excluded_by_name` |  |
| EXP-11 | S19 | closed | `test_native_mcp_session.py`: `test_no_mcp_reads_no_file_and_starts_no_process`; `test_native_mcp_session_command.py`: `test_there_is_no_mcp_command_when_mcp_is_off` |  |
| EXP-12 | S19 | closed, with limits | `test_native_mcp_session.py`: `test_the_builtin_mcp_entry_turns_mcp_off_and_on`; `test_native_mcp_discovery.py`: `test_settings_read_the_builtin_entry_per_scope`, `test_config_command_lists_and_toggles_the_builtin_mcp_entry`, `test_run_options_decide_whether_mcp_is_on`; `test_native_mcp_discovery.py`: `test_builtin_extension_enablement`, `test_config_command_replaces_every_exact_entry_of_the_builtin` (r3 F1, which also extends `test_settings_read_the_builtin_entry_per_scope`) | The Built-in list is `pipy config list` and `pipy config enable\|disable builtin mcp`; pipy has no interactive config list. DEV-32. |
| EXP-13 | G8 | closed, with limits | `test_native_mcp_session.py`: `test_the_builtin_tool_search_entry_turns_tool_search_off_and_on`, `test_the_builtin_codemode_entry_turns_codemode_off`; `test_native_tool_capabilities_mcp.py`: `test_builtin_extensions_decide_on_codemode_and_tool_search_per_value`, `test_register_codemode_follows_a_reload_published_during_the_probe`; `test_native_mcp_discovery.py`: `test_run_options_decide_whether_codemode_is_loaded` | `pipy config list` and `pipy config enable\|disable builtin <name>` in place of an interactive list. DEV-32. |

### `tool_search` (TSR)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| TSR-01 | S09 | closed | `test_native_tool_search_tool.py`: `test_definition_is_the_fixed_pi_declaration`, `test_registered_inactive_until_named`, `test_scripts_cannot_call_it`; `test_native_mcp_session.py`: `test_tool_search_is_activated_for_deferred_tools` |  |
| TSR-02 | S09 | closed | `test_native_tool_search_tool.py`: `test_definition_is_the_fixed_pi_declaration`, `test_cli_names_it_and_the_prompt_lists_it` |  |
| TSR-03 | S09 | closed, with limits | `test_native_tool_search_tool.py`: `test_loadable_tools_are_the_inactive_codemode_and_deferred_ones`, `test_loads_the_matches_and_reports_first_description_lines`, `test_never_loads_direct_model_only_or_hidden_tools` | DEV-29. |
| TSR-04 | S09 | closed, with limits | `test_native_tool_search_tool.py`: `test_loads_the_matches_and_reports_first_description_lines`, `test_limit_bounds_the_loaded_tools` | DEV-29. DEV-77. |
| TSR-05 | S09 | closed | `test_native_tool_search_tool.py`: `test_an_empty_query_fails`, `test_a_limit_that_is_not_a_positive_integer_fails` |  |
| TSR-06 | S06 | closed | `test_native_tool_search.py`: `test_tokenize_splits_names_drops_stop_words_and_folds_plurals`, `test_tokenize_drops_each_of_the_21_stop_words`, `test_stem_folds_plurals` |  |
| TSR-07 | S06 | closed | `test_native_tool_search.py`: `test_bm25_scores_follow_the_okapi_formula`, `test_ranks_by_term_relevance_and_respects_the_limit`, `test_ties_keep_registration_order`, `test_only_matching_documents_are_returned` |  |
| TSR-08 | S06 | closed, with limits | `test_native_tool_search.py`: `test_document_text_lists_name_description_and_schema_in_order`, `test_document_text_follows_items_and_schema_combinators`, `test_document_text_includes_namespace_description_and_instructions`, `test_a_server_description_ranks_that_servers_tools` | DEV-29. |
| TSR-09 | S09 | closed, with limits | `test_native_tool_search_tool.py`: `test_session_loads_a_deferred_tool_and_keeps_it_declared_on_the_branch` | A session resumed in a new process starts from the configured tools, as in Pi's CLI and SDK. DEV-30. |
| TSR-10 | S22 | closed, with limits | `test_native_mcp_reload.py`: `test_tools_tool_search_loaded_are_declared_again_after_reload`, `test_a_loaded_tool_that_registers_after_the_prompt_started_stays_inactive`; `test_native_tool_capabilities_mcp.py`: `test_a_restored_transcript_declares_its_tools_once_their_server_connects` | After resume in a new process nothing is restored from the transcript (Pi's CLI does the same); `/reload` and `/tree` restore. DEV-30. |
| TSR-11 | S09 | closed | `test_native_tool_search_tool.py`: `test_finds_nothing_when_every_match_is_already_declared` |  |
| TSR-12 | G8 | closed | `test_native_mcp_manager.py`: `test_a_loaded_tool_is_not_declared_again_when_its_server_is_enabled_again`; `test_native_tool_capabilities_mcp.py`: `test_a_withdrawn_tool_is_gone_and_has_to_be_loaded_again_when_it_comes_back`; `test_native_mcp_manager_resources.py`: `test_a_loaded_resource_tool_leaves_the_selection_when_it_is_hidden` (R4) |  |

### Codemode reaching other tools (CMD)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| CMD-01 | S03 | closed | `test_native_codemode_extension_tools.py`: `test_script_reaches_neither_itself_nor_inactive_or_model_only_tools`; `test_native_agent_nested_calls.py`: `test_composite_never_reaches_itself`, `test_uncallable_and_request_withheld_tools_are_refused`; `test_native_mcp_session.py`: `test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model` |  |
| CMD-02 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_tools_are_reached_by_identifier_and_by_raw_name`, `test_tool_table_maps_identifiers_and_raw_names_first_tool_wins`, `test_a_keyword_gets_a_trailing_underscore_as_its_identifier` | DEV-01: Python identifiers. DEV-27. |
| CMD-03 | S04, G5 | closed, with limits | `test_native_codemode_result_shapes.py`: `test_script_receives_structured_content_text_or_a_tool_error` (a null result since G5), `test_a_script_gets_bashs_structured_result_also_for_a_failed_command`, `test_a_script_reads_an_image_and_shows_it` (G5); `test_native_agent_nested_calls.py`: `test_structured_content_follows_the_result_policy_and_the_output_schema`; `test_native_codemode_host_runtime.py`: `test_a_large_non_ascii_result_reaches_the_script` (R1) | DEV-59. A result without structured content carries `NO_STRUCTURED_CONTENT`; `None` is `null` (G5). A reply counts UTF-8 bytes against its 16 MiB (R1). |
| CMD-04 | S04 | closed, with limits | `test_native_codemode_result_shapes.py`: `test_script_receives_structured_content_text_or_a_tool_error`; `test_native_agent_nested_calls.py`: `test_a_failed_call_without_error_text_is_recorded_as_failed` | DEV-59. An extension tool's text is bounded at 64 KiB. DEV-73. |
| CMD-05 | S16 | closed | `test_native_mcp_tools.py`: `test_an_error_result_is_an_error_that_keeps_the_result_for_scripts`, `test_converts_results_passing_the_call_tool_result_to_scripts`; `test_native_mcp_session.py`: `test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model` |  |
| CMD-06 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_script_tools_carry_the_description_and_the_declaration`, `test_script_lists_identifiers_namespaces_and_argument_errors`; `test_native_codemode_description.py`: `test_discovery_descriptions_carry_the_prompt_guidelines`; `test_native_extension_tool_prompts.py`: `test_scripts_see_an_extension_tools_guidelines` (G9) | DEV-27. |
| CMD-07 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_search_tools_ranks_callable_tools_and_returns_all_tools_entries`, `test_discovery_argument_errors_use_pis_messages`, `test_script_finds_tools_with_search_tools_and_describe_tool` | DEV-27. |
| CMD-08 | S07 | closed | `test_native_codemode_discovery.py`: `test_namespace_is_matched_by_name_or_alias_as_written_or_as_identifier` |  |
| CMD-09 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_describe_tool_takes_a_raw_name_or_an_identifier` | DEV-27. |
| CMD-10 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_namespace_filter_and_description_for_other_names`, `test_script_lists_identifiers_namespaces_and_argument_errors`; `test_native_codemode_description.py`: `test_leaves_namespace_instructions_out` | DEV-27. |
| CMD-11 | S05 | closed, with limits | `test_native_codemode_declarations.py`: `test_renders_objects_on_one_line_with_sorted_properties`, `test_puts_property_descriptions_on_comment_lines`, `test_renders_signatures_with_normalized_identifiers` | DEV-01: Python-style types with `#` comment lines. |
| CMD-12 | S05 | closed | `test_native_codemode_declarations.py`: `test_puts_constraints_the_type_cannot_express_on_property_comment_lines`, `test_tags_cover_every_keyword_in_pi_order` |  |
| CMD-13 | S05 | closed, with limits | `test_native_codemode_declarations.py`: `test_reads_constraints_of_optional_fields_from_the_non_null_branch`, `test_reads_draft_04_boolean_exclusive_bounds`, `test_leaves_out_long_tags_and_escapes_line_terminators` | DEV-28. |
| CMD-14 | S05 | closed | `test_native_codemode_declarations.py`: `test_renders_primitives_literals_and_unions`, `test_renders_arrays_and_tuples`, `test_resolves_local_references_and_stops_at_recursive_ones`, `test_more_than_32_references_render_as_any` |  |
| CMD-15 | S05 | closed, with limits | `test_native_codemode_declarations.py`: `test_drops_constraint_tags_before_rendering_a_type_over_the_budget_as_unknown`, `test_the_default_input_budget_drops_tags_and_then_the_type` | DEV-28. |
| CMD-16 | S16 | closed | `test_native_codemode_declarations.py`: `test_renders_output_schemas_shaped_like_an_mcp_call_tool_result_as_plain_types`; `test_native_mcp_tools.py`: `test_declares_the_call_tool_result_scripts_receive` |  |
| CMD-17 | S07 | closed, with limits | `test_native_codemode_discovery.py`: `test_a_missing_tool_names_close_matches` | DEV-27. |
| CMD-18 | S08 | closed | `test_native_codemode_description.py`: `test_description_explains_the_call_contract_and_names_the_helpers` |  |
| CMD-19 | S08 | closed, with limits | `test_native_codemode_description.py`: `test_lists_everything_without_a_budget`, `test_sections_carry_guidelines_and_the_key_that_reaches_the_tool`; `test_native_extension_tool_prompts.py`: `test_scripts_see_an_extension_tools_guidelines` (G9) | DEV-28. |
| CMD-20 | S08 | closed | `test_native_codemode_description.py`: `test_fills_the_budget_round_robin_cheapest_first`, `test_a_group_whose_next_tool_does_not_fit_drops_out`, `test_session_inline_budget_setting` |  |
| CMD-21 | S08 | closed | `test_native_codemode_description.py`: `test_leaves_deferred_tools_and_their_namespaces_out_entirely`, `test_leaves_namespace_instructions_out`, `test_description_is_byte_identical_when_a_deferred_tool_registers` |  |
| CMD-22 | S08 | closed, with limits | `test_native_codemode_description.py`: `test_presents_callable_tools_per_codemode_mode`, `test_script_call_line_says_what_a_call_resolves_to`, `test_session_request_with_mode_only_leaves_direct_declarations_out`, `test_tools_hidden_by_mode_only_stay_callable_from_a_script` | With `only`, a direct model call to a tool whose declaration is left out is an unknown tool in pipy (Pi would run it). Settings are read at session start and on `/reload`, not before every prompt. DEV-23. |
| CMD-23 | S16 | closed | `test_native_mcp_tools.py`: `test_a_direct_tool_tells_scripts_what_a_call_resolves_to` |  |
| CMD-24 | S03, G6, R5, T5 | closed | `test_native_codemode_extension_tools.py`: `test_script_calls_extension_tools_through_the_hooks`; `test_native_tool_call_hook_arguments.py`: `test_a_tool_call_hook_sees_the_call_id_and_the_calling_tools_id` (G6); `test_native_agent_nested_calls.py`: `test_nested_calls_are_validated_and_the_policy_gets_their_parent_id` (G6); `test_native_agent_nested_calls.py`: `test_nested_pipeline_statuses_unbudgeted_and_parent_only_records`, `test_concurrent_nested_calls_run_past_the_budget` (T5); `test_native_mcp_session.py`: `test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model`; `test_native_codemode_composite.py`: `test_submitted_calls_run_at_the_same_time_and_the_record_keeps_their_order`, `test_a_script_makes_more_calls_than_the_tool_budget_has_places` (G3, T5); `test_native_codemode_host_runtime.py`: `test_a_call_may_carry_more_than_the_line_limit` (R1); `test_native_agent_event_adapters.py`: `test_automation_projection_emits_pi_tool_result_objects`; `test_native_automation_json_mode.py`: `test_json_mode_tool_events_carry_pi_result_objects` (R5) |  |
| CMD-25 | S04, G6 | closed, with limits | `test_native_extension_tool_result_hooks.py`: `test_tool_result_hooks_see_and_decide_the_structured_content`, `test_a_tool_result_hook_sees_the_call_and_changes_every_field` (G6); `test_native_agent_nested_calls.py`: `test_structured_content_follows_the_result_policy_and_the_output_schema` | Pi's transform can also replace usage; pipy counts no usage of tools. DEV-22. |
| CMD-26 | S03 | closed | `test_native_codemode_extension_tools.py`: `test_script_calls_extension_tools_through_the_hooks`; `test_native_agent_nested_calls.py`: `test_transient_records_bound_argument_bytes_and_drop_mutable_details`; `test_native_nested_lifecycle.py`: `test_the_live_row_keeps_the_latest_calls_past_the_record_bound`, `test_live_status_args_bounds_sanitize_and_parent_mismatch` (r3 D6) |  |
| CMD-27 | S08 | closed, with limits | `test_native_codemode_description.py`: `test_prompt_contribution_is_one_snippet_and_does_not_depend_on_tools` | Pipy's contribution has two guideline lines; the second predates this plan (codemode adherence). MCP adds nothing. DEV-24. |
| CMD-28 | S39 | closed | `test_native_codemode_images.py`: `test_image_accepts_data_urls_image_url_objects_and_image_blocks`, `test_image_rejects_everything_else_with_pis_messages`, `test_a_real_script_shows_images_and_gets_errors_for_what_is_not_one` |  |
| CMD-29 | S39 | closed, with limits | `test_native_codemode_images.py`: `test_images_stay_in_output_order_each_after_its_saved_path`, `test_a_saved_image_is_a_file_only_its_user_can_read`, `test_a_failed_save_is_named_and_keeps_the_image`, `test_a_session_attaches_only_the_images_the_script_shows`; `test_native_codemode_result.py`: `test_text_items_are_marked_and_console_output_comes_last` (R1); `test_native_codemode_host_runtime.py`: `test_printed_output_is_console_output_and_text_items_are_marked` (R1) | Text and images share Pi's output limit of 16 Mi characters; the `==> text N/M <==` lines and the `<console_output>` block of Pi's `formatOutput` since R1. A script that shows some hundreds of images overflows the result's 64 KiB of text. DEV-20. |
| CMD-30 | S39 | closed, with limits | `test_native_codemode_images.py`: `test_images_follow_a_truncated_text_and_are_never_cut` | DEV-20. `max_output_tokens` is at most 16000. DEV-80. |

### Resources (RES)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| RES-01 | S23 | closed | `test_native_mcp_manager_resources.py`: `test_the_resource_tools_exist_once_a_server_with_resources_connected`, `test_a_dropped_server_keeps_the_resource_tools_and_a_read_reconnects_it`, `test_disabling_the_only_server_with_resources_makes_the_tools_unreachable` |  |
| RES-02 | S23 | closed | `test_native_mcp_manager_resources.py`: `test_the_tools_have_the_widest_exposure_of_the_servers_they_reach`, `test_the_tools_leave_the_declared_set_when_they_stop_being_direct` |  |
| RES-03 | S23 | closed | `test_native_mcp_resources.py`: `test_the_three_tools_have_pis_names_schemas_and_the_read_only_hint` |  |
| RES-04 | S23 | closed, with limits | `test_native_mcp_resources.py`: `test_one_server_lists_one_page_and_the_cursor_continues`, `test_without_a_server_every_page_of_every_server_is_listed_in_name_order`, `test_a_cursor_needs_a_server`; `test_native_mcp_manager_resources.py`: `test_every_server_is_listed_in_name_order_with_the_ones_that_fail` | DEV-58. |
| RES-05 | S23 | closed | `test_native_mcp_resources.py`: `test_one_server_lists_one_page_and_the_cursor_continues`, `test_mcp_app_resources_are_recognized` |  |
| RES-06 | S23 | closed | `test_native_mcp_resources.py`: `test_templates_are_listed_like_resources`; `test_native_mcp_connection_resources.py`: `test_a_server_without_the_templates_method_has_no_templates` |  |
| RES-07 | S23 | closed, with limits | `test_native_mcp_resources.py`: `test_a_long_listing_is_cut_in_the_middle_for_the_model_only`; `test_native_mcp_session_resources.py`: `test_the_resource_tools_are_callable_from_codemode_for_codemode_servers` | DEV-58. |
| RES-08 | S23 | closed, with limits | `test_native_mcp_resources.py`: `test_read_needs_a_server_and_a_uri`, `test_arguments_that_are_not_strings_are_refused`, `test_an_unknown_server_names_the_servers_with_resources` | DEV-58. |
| RES-09 | S23, S39 | closed, with limits | `test_native_mcp_resources.py`: `test_text_is_read_as_text_and_scripts_get_the_contents_without_meta`, `test_several_contents_are_each_preceded_by_their_uri`, `test_an_empty_resource_says_so`; `test_native_mcp_session_images.py`: `test_mcp_images_reach_every_adapter_family_and_survive_a_resume` | As TLS-11 for images. DEV-19. |
| RES-10 | S23 | closed, with limits | `test_native_mcp_connection_resources.py`: `test_a_connection_lists_the_resources_when_it_connects`, `test_a_server_whose_listings_fail_still_connects`, `test_a_resource_list_change_lists_the_resources_again`; `test_native_mcp_connection_resources.py`: `test_a_slow_tool_listing_does_not_delay_a_resource_refresh` (L1) | DEV-58. |

### OAuth (OAU)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| OAU-01 | S30 | closed | `test_native_mcp_auth_provider.py`: `test_sends_the_stored_access_token`, `test_refreshes_the_tokens_after_a_401`, `test_a_401_without_a_refresh_token_needs_a_sign_in`; `test_native_mcp_connection_oauth.py`: `test_connects_with_the_stored_token_and_refreshes_it_when_it_is_rejected` |  |
| OAU-02 | S27 | closed, with limits | `test_native_mcp_oauth_discovery.py`: `test_protected_resource_metadata_is_found_below_the_server_path`, `test_protected_resource_metadata_falls_back_to_the_root_form`, `test_the_challenge_s_metadata_url_is_asked_instead_of_the_well_known_ones`, `test_the_metadata_request_carries_the_given_protocol_version`, `test_a_network_failure_loading_resource_metadata_fails_discovery` | A metadata response over 1 MiB is refused. DEV-72. No proxy support (DEV-82). |
| OAU-03 | S27 | closed, with limits | `test_native_mcp_oauth_discovery.py`: `test_discovery_finds_the_authorization_server_the_resource_names`, `test_without_resource_metadata_the_server_s_origin_is_the_authorization_server`, `test_an_issuer_with_a_path_has_three_candidate_urls`, `test_another_failure_status_ends_the_search`; `test_native_mcp_oauth_flow.py`: `test_a_server_without_metadata_gets_the_default_endpoints` | A metadata response over 1 MiB is refused. DEV-72. No proxy support (DEV-82). |
| OAU-04 | S27 | closed | `test_native_mcp_oauth_discovery.py`: `test_rejects_authorization_metadata_whose_issuer_does_not_match_discovery`, `test_the_issuer_may_differ_by_one_trailing_slash` |  |
| OAU-05 | S27 | closed | `test_native_mcp_oauth_discovery.py`: `test_a_bearer_challenge_is_parsed`, `test_a_step_up_challenge_is_parsed`, `test_empty_challenge_values_count_as_absent`, `test_no_challenge_without_a_bearer_or_dpop_header` |  |
| OAU-06 | S27, S28 | closed | `test_native_mcp_oauth_discovery.py`: `test_a_resource_at_or_above_the_server_url_is_selected`, `test_a_resource_that_is_not_the_server_s_fails`; `test_native_mcp_oauth_flow.py`: `test_resource_metadata_for_another_resource_fails_the_flow`, `test_discovers_registers_authorizes_with_pkce_and_refreshes` |  |
| OAU-07 | S28, S31 | closed | `test_native_mcp_oauth_flow.py`: `test_the_requested_scope_is_the_first_source_that_has_one`; `test_native_mcp_sign_in.py`: `test_asks_for_the_configured_scope_and_the_challenged_one_together` |  |
| OAU-08 | S28 | closed, with limits | `test_native_mcp_oauth_flow.py`: `test_the_registration_request_carries_the_client_metadata_and_the_scope`, `test_registers_with_an_application_type_derived_from_the_redirect_uris`, `test_a_server_without_a_registration_endpoint_cannot_register`, `test_a_refused_registration_reports_status_and_body`; `test_native_mcp_auth_provider.py`: `test_the_client_of_a_registration_is_named_pipy_unless_configured` | DEV-05: the default `client_name` is `pipy`. A registration response over 1 MiB is refused. DEV-72. No proxy support (DEV-82). |
| OAU-09 | S28 | closed | `test_native_mcp_oauth_flow.py`: `test_the_authorization_url_carries_the_request`, `test_offline_access_asks_for_consent`, `test_a_server_without_the_code_response_type_fails`, `test_a_server_without_s256_fails` |  |
| OAU-10 | S28 | closed | `test_native_mcp_oauth_flow.py`: `test_tokens_are_not_requested_from_an_insecure_endpoint`, `test_tokens_are_requested_from_https_and_loopback_endpoints`, `test_a_client_with_a_secret_authenticates_as_registered_or_as_the_server_supports`, `test_a_client_without_a_secret_sends_its_id_only` |  |
| OAU-11 | S28 | closed, with limits | `test_native_mcp_oauth_flow.py`: `test_an_error_field_is_an_oauth_error_whatever_the_status`, `test_another_failure_status_is_a_server_error`, `test_empty_and_null_optional_token_fields_count_as_absent` | `expires_in` is a number or a decimal string (DEV-70); a token response over 1 MiB is refused (DEV-72). No proxy support (DEV-82). |
| OAU-12 | S28 | closed | `test_native_mcp_oauth_flow.py`: `test_exchanges_a_code_only_when_its_iss_parameter_names_the_authorization_server`; `test_native_mcp_sign_in.py`: `test_rejects_an_authorization_response_from_another_issuer` |  |
| OAU-13 | S28 | closed | `test_native_mcp_oauth_flow.py`: `test_a_token_response_without_scope_records_the_requested_scope`, `test_a_refresh_stores_the_rotated_refresh_token`; `test_native_mcp_oauth_provider.py`: `test_a_refresh_through_the_provider_stores_the_rotated_tokens` |  |
| OAU-14 | S28 | closed | `test_native_mcp_oauth_flow.py`: `test_a_refresh_token_the_server_no_longer_knows_asks_for_authorization`, `test_a_client_the_server_refuses_is_registered_again`, `test_the_flow_runs_again_only_once`, `test_a_refresh_that_fails_otherwise_asks_for_authorization`, `test_another_oauth_error_of_a_refresh_is_reported` |  |
| OAU-15 | S28, S31 | closed, with limits | `test_native_mcp_oauth_provider.py`: `test_the_state_parameter_is_32_random_bytes_in_hex`, `test_nothing_stored_for_another_server_url_is_used`; `test_native_mcp_sign_in.py`: `test_every_sign_in_has_a_new_state_and_reuses_the_registered_client` | The stored state's server URL is compared without its fragment. DEV-69. |
| OAU-16 | S29 | closed | `test_native_mcp_oauth_callback.py`: `test_listens_on_a_free_loopback_port_at_callback`, `test_another_path_is_not_found`, `test_an_unknown_or_missing_state_is_refused`, `test_an_error_response_fails_the_wait_with_its_description`, `test_rejects_a_response_on_another_path_than_the_expected_one`, `test_a_wait_gives_up_after_the_timeout` |  |
| OAU-17 | S29 | closed, with limits | `test_native_mcp_oauth_callback.py`: `test_the_sign_in_pages_are_html`, `test_renders_pages_through_render_page` | The pages have Pi's layout without its logo. DEV-53. |
| OAU-18 | S30 | closed, with limits | `test_native_mcp_credentials.py`: `test_the_file_is_private_and_readable_json`, `test_keeps_separate_credentials_for_servers_sharing_a_url`, `test_a_server_with_the_same_name_and_url_has_one_entry_in_every_store`; `test_native_mcp_manager_oauth.py`: `test_sign_out_deletes_the_credentials_and_the_server_needs_a_sign_in`; `test_native_mcp_credentials.py`: `test_moves_credentials_stored_by_the_url_to_the_first_server_that_loads_them`, `test_signs_out_of_credentials_stored_by_the_url` (L2) | DEV-55. The key has no fragment. DEV-69. |
| OAU-19 | S30 | closed | `test_native_mcp_auth_provider.py`: `test_refreshes_a_token_that_is_about_to_expire`, `test_a_failed_refresh_sends_the_old_token` |  |
| OAU-20 | S30 | closed, with limits | `test_native_mcp_auth_provider.py`: `test_the_requests_of_one_process_share_one_refresh`, `test_two_processes_refresh_a_rejected_token_once`, `test_tokens_that_changed_meanwhile_are_used_without_refreshing`, `test_a_refresh_gives_up_when_the_lock_stays_taken`; `test_native_mcp_credentials.py`: `test_another_process_is_kept_out_and_its_lock_is_free_when_it_dies` | DEV-14: the lock is a `flock`; the lock of a dead holder is free at once instead of after 20 s. DEV-55. |
| OAU-21 | S31 | closed | `test_native_mcp_auth_provider.py`: `test_a_challenge_for_more_scope_needs_a_sign_in`; `test_native_mcp_sign_in.py`: `test_keeps_the_granted_scope_when_the_server_asks_for_more`, `test_a_step_up_adds_the_configured_scope_too` |  |
| OAU-22 | S30 | closed | `test_native_mcp_auth_provider.py`: `test_each_request_to_the_authorization_server_has_a_time_limit`; `test_native_mcp_sign_in.py`: `test_cancels_a_sign_in_while_the_authorization_server_hangs` |  |
| OAU-23 | S30 | closed, with limits | `test_native_mcp_connection_oauth.py`: `test_closing_waits_for_a_running_refresh_to_save_its_tokens`, `test_closes_without_refreshing_an_expiring_token` | DEV-55. |
| OAU-24 | S31 | closed | `test_native_mcp_sign_in.py`: `test_tries_the_stored_refresh_token_before_the_browser`; `test_native_mcp_oauth_flow.py`: `test_asks_for_authorization_instead_of_refreshing_when_the_server_needs_more_scope` |  |
| OAU-25 | S31 | closed | `test_native_mcp_sign_in.py`: `test_callback_settings`, `test_a_taken_port_is_replaced_by_a_free_one_and_the_client_registered_again`, `test_a_configured_port_that_is_taken_fails_the_sign_in`, `test_a_configured_client_keeps_its_tokens_on_another_redirect_uri` |  |
| OAU-26 | S31 | closed, with limits | `test_native_mcp_sign_in.py`: `test_signs_in_through_the_browser`, `test_accepts_a_pasted_redirect_url_when_the_browser_cannot_reach_the_callback` | A callback path is compared as written, not percent-encoded. DEV-54. |
| OAU-27 | S31 | closed | `test_native_mcp_sign_in.py`: `test_rejects_a_pasted_url_that_is_not_this_sign_ins_response`, `test_reads_the_authorization_response_from_a_pasted_redirect_url`, `test_an_empty_paste_cancels_the_sign_in` |  |
| OAU-28 | S31 | closed | `test_native_mcp_sign_in.py`: `test_cancels_a_sign_in_that_waits_for_the_browser`, `test_cancels_a_sign_in_while_the_authorization_server_hangs`; `test_native_mcp_manager_oauth.py`: `test_closing_the_manager_cancels_a_running_sign_in`, `test_a_cancelled_sign_in_says_so` |  |
| OAU-29 | S31 | closed | `test_native_mcp_manager_oauth.py`: `test_signs_in_through_the_browser_and_connects_the_server`, `test_reports_a_reconnect_that_fails_after_the_sign_in`, `test_reports_a_sign_in_that_failed`, `test_only_an_enabled_oauth_server_can_be_signed_in_to` |  |
| OAU-30 | S32 | closed | `test_native_mcp_oauth_options.py`: `test_a_configured_client_is_used_without_a_registration`; `test_native_mcp_oauth_provider.py`: `test_a_configured_client_is_used_and_never_stored`; `test_native_mcp_connection_oauth.py`: `test_a_client_secret_that_cannot_be_resolved_fails_the_refresh_only` |  |
| OAU-31 | S32 | closed, with limits | `test_native_mcp_oauth_options.py`: `test_a_callback_port_alone_fixes_the_default_redirect_uri`, `test_uses_the_configured_callback_url_and_scope`, `test_adds_the_listening_port_to_a_callback_url_without_one` | A callback path is compared as written, not percent-encoded. DEV-54. |
| OAU-32 | S32 | closed | `test_native_mcp_oauth_options.py`: `test_registers_with_the_configured_client_name`, `test_a_changed_client_name_applies_only_to_a_new_registration` |  |
| OAU-33 | S32 | closed, with limits | `test_native_mcp_oauth_options.py`: `test_uses_the_document_when_authorization_responses_name_their_issuer`, `test_fails_instead_of_registering_when_the_server_does_not_support_documents`, `test_a_server_without_documents_for_public_clients_is_refused` | DEV-06: the documents are the user's, named with `oauth.clientMetadataBaseUrl`; pipy has no built-in document (owner question Q1). |
| OAU-34 | S32 | closed, with limits | `test_native_mcp_oauth_options.py`: `test_uses_a_document_and_callback_path_of_the_server_without_the_iss_parameter`, `test_the_callback_id_is_computed_over_the_server_url_without_its_fragment`, `test_accepts_the_authorization_response_only_on_the_server_specific_redirect_uri`, `test_replaces_a_registered_client_when_switching_to_the_document` | The `<id>` hashes the URL as pipy's parser writes it, which for unusual URLs may differ from Pi's. DEV-54. |
| OAU-35 | S32 | closed | `test_native_mcp_oauth_options.py`: `test_uses_the_configured_authorization_server_metadata_url`, `test_the_configured_metadata_is_used_as_written_and_never_cached` |  |
| OAU-36 | S33 | closed, with limits | `test_native_mcp_provider_auth.py`: `test_sends_the_provider_token_and_asks_for_the_provider_login_when_the_server_rejects_it`, `test_the_token_is_read_for_every_request`, `test_a_session_sends_the_login_of_the_provider_and_reports_a_missing_one`; `test_native_mcp_provider_auth.py`: `test_the_models_json_key_is_the_token_when_no_other_source_has_one`, `test_the_codex_login_is_the_token_of_openai_codex` (L4); `test_native_mcp_provider_auth.py`: `test_the_api_key_given_to_the_session_is_the_token_of_its_provider_only`, `test_the_api_key_given_to_the_session_is_never_sent_for_another_provider`, `test_the_session_api_key_belongs_to_the_provider_the_session_starts_with`, `test_every_built_in_login_is_refreshed_and_saved_once_it_expires_soon`, `test_an_extension_login_is_refreshed_and_saved_through_the_extension` (H1); `test_native_mcp_provider_auth.py`: `test_an_auth_file_with_a_byte_order_mark_is_read` (r3 Q4) | The key a model request to the provider would use; `--api-key` only for the starting model's provider; every stored login refreshed as Pi does; no ambient cloud credentials. DEV-52. A stored key is resolved by pipy's rules, without Pi's `${VAR}` templates and `env` map. DEV-83. |
| OAU-37 | L2 | closed, with limits | `test_native_mcp_oauth_adapter.py`: `test_authorizes_a_transport_and_refreshes_after_a_401`, `test_shares_one_refresh_between_concurrent_401s_when_refresh_tokens_rotate`, `test_asks_for_authorization_instead_of_refreshing_when_the_server_needs_more_scope`, `test_a_request_whose_token_was_replaced_is_sent_again_without_a_refresh` | The flow uses the OAuth library's fetch, not the transport's. DEV-67. |

### `pipy mcp` shell commands (CMDL)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| CMDL-01 | S20a | closed, with limits | `test_native_mcp_shell_cli.py`: `test_prints_the_usage_text`, `test_rejects_unknown_commands`, `test_pipy_help_lists_the_mcp_command` | DEV-57. |
| CMDL-02 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_pipy_mcp_runs_without_a_session_in_the_current_directory`; `test_native_mcp_provider_auth.py`: `test_pipy_mcp_list_sends_no_provider_token` |  |
| CMDL-03 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_adds_stdio_servers_and_passes_options_after_the_command_through`, `test_adds_http_servers_and_keeps_other_content_of_the_file`, `test_rejects_invalid_add_invocations_without_writing` |  |
| CMDL-04 | S20a | closed, with limits | `test_native_mcp_shell_cli.py`: `test_adds_stdio_servers_and_passes_options_after_the_command_through`, `test_adds_http_servers_and_keeps_other_content_of_the_file`, `test_rejects_invalid_add_invocations_without_writing` | DEV-57. |
| CMDL-05 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_rejects_invalid_add_invocations_without_writing` |  |
| CMDL-06 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_adds_and_removes_project_servers`, `test_adds_http_servers_and_keeps_other_content_of_the_file`, `test_adds_stdio_servers_and_passes_options_after_the_command_through` |  |
| CMDL-07 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_removes_global_servers`, `test_adds_and_removes_project_servers`, `test_rejects_invalid_remove_invocations` |  |
| CMDL-08 | S20a, S23, S26 | closed | `test_native_mcp_shell_cli.py`: `test_lists_servers_with_state_tools_and_errors_and_fails_while_anything_is_wrong`, `test_list_marks_tools_whose_exposure_differs_and_names_the_override`, `test_list_counts_resources_and_templates_of_a_server_that_offers_them`, `test_list_connects_to_http_servers`, `test_list_reports_an_oauth_server_that_wants_a_sign_in` |  |
| CMDL-09 | S20a, S23, S26 | closed | `test_native_mcp_shell_cli.py`: `test_prints_json_for_scripts`, `test_json_reports_failures_errors_and_the_ignored_project_file`, `test_list_counts_resources_and_templates_of_a_server_that_offers_them` |  |
| CMDL-10 | S20a | closed | `test_native_mcp_shell_cli.py`: `test_lists_servers_with_state_tools_and_errors_and_fails_while_anything_is_wrong`, `test_list_without_servers_says_where_to_add_them` |  |
| CMDL-11 | S20a | closed, with limits | `test_native_mcp_shell_cli.py`: `test_list_reads_the_project_file_only_in_a_trusted_project`, `test_login_and_logout_name_the_ignored_project_file_and_no_servers`, `test_the_trust_decision_is_read_from_the_commands_config_home` | DEV-57. |
| CMDL-12 | S31 | closed | `test_native_mcp_shell_cli.py`: `test_rejects_unknown_servers_and_servers_without_oauth_for_login_and_logout`, `test_login_reports_a_server_that_fails_for_another_reason`, `test_list_connects_to_an_oauth_server_with_the_stored_token` |  |
| CMDL-13 | S31 | closed, with limits | `test_native_mcp_shell_cli.py`: `test_signs_in_through_the_browser_and_out_again`, `test_reads_the_pasted_redirect_url_from_a_terminal`, `test_the_login_timeout_must_be_a_positive_number`, `test_gives_up_on_login_after_the_timeout_also_while_the_server_hangs` | DEV-57. |
| CMDL-14 | S31 | closed, with limits | `test_native_mcp_shell_cli.py`: `test_signs_in_through_the_browser_and_out_again`, `test_logout_reports_a_credentials_file_it_cannot_read` | DEV-55. |

### `/mcp` in a session (SLH)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| SLH-01 | S21 | closed, with limits | `test_native_mcp_commands.py`: `test_the_command_is_described_as_in_pi`, `test_arguments_complete_the_actions_then_the_eligible_servers`, `test_login_and_logout_complete_the_servers_that_use_oauth` | DEV-38. |
| SLH-02 | S21 | closed, with limits | `test_native_mcp_commands.py`: `test_status_has_one_line_per_server_for_each_state`, `test_mcp_waits_for_the_startup_connections_and_prints_the_status`, `test_status_without_servers_says_where_to_add_them`; `test_native_mcp_session_command.py`: `test_mcp_prints_one_line_per_server` | In a terminal `/mcp` opens the manager (MGR rows). DEV-17, DEV-38. |
| SLH-03 | S21 | closed, with limits | `test_native_mcp_commands.py`: `test_reconnect_reports_the_new_state`, `test_reconnect_reports_the_error_of_a_failed_connect`; `test_native_mcp_session_command.py`: `test_reconnect_after_the_server_process_ended` | DEV-17. |
| SLH-04 | S31 | closed | `test_native_mcp_commands.py`: `test_login_shows_the_url_opens_the_browser_and_reports_the_tools`, `test_login_requires_a_user_interface`, `test_login_asks_for_the_pasted_redirect_url_after_showing_the_url`, `test_logout_deletes_the_credentials_and_says_whether_there_were_any` |  |
| SLH-05 | S21 | closed | `test_native_mcp_commands.py`: `test_the_one_failed_server_is_preferred`, `test_several_candidates_are_offered_for_selection`, `test_an_unknown_or_ineligible_name_is_an_error`, `test_without_an_eligible_server_the_message_is_a_notice`, `test_login_without_a_name_takes_the_server_that_needs_a_sign_in` |  |
| SLH-06 | S21 | closed, with limits | `test_native_mcp_commands.py`: `test_too_many_arguments_or_an_unknown_action_prints_the_usage` | DEV-38. |
| SLH-07 | X1, N2 | closed | `test_native_automation_prompt_dispatch.py`: `test_print_mode_runs_mcp_and_starts_no_run`, `test_an_extension_command_runs_with_its_arguments`, `test_a_failing_extension_command_is_still_handled`; `test_native_automation_rpc_prompt_dispatch.py`: `test_mcp_is_handled_and_starts_no_run`, `test_a_failing_command_is_handled_too`; `test_native_extension_mcp_servers.py`: `test_an_unconnected_server_is_an_extension_error_of_print_mode`; `test_native_extension_dispatch.py`: `test_an_exception_whose_text_raises_is_still_bounded`; `test_native_coding_session_controller.py`: `test_prompt_reports_a_failed_extension_command_and_starts_no_run` |  |
| SLH-08 | X1 | closed | `test_native_automation_prompt_dispatch.py`: `test_other_slash_texts_are_the_prompt`, `test_json_mode_sends_an_unknown_slash_name_to_the_model`; `test_native_automation_rpc_prompt_dispatch.py`: `test_other_slash_texts_start_a_run` |  |
| SLH-09 | X1, T5 | closed, with limits | `test_native_automation_prompt_dispatch.py`: `test_a_prompt_template_and_a_skill_are_expanded`, `test_an_extension_command_comes_before_a_template_of_its_name`; `test_native_automation_rpc_prompt_dispatch.py`: `test_a_prompt_template_is_expanded`; `test_native_automation_prompt_dispatch.py`: `test_input_hooks_run_before_expansion`, `test_a_template_named_like_a_builtin_command_is_expanded`; `test_native_coding_session_controller.py`: `test_prompt_input_hooks_see_the_unexpanded_text` (N2); `test_native_coding_session_controller.py`: `test_a_typed_template_line_meets_the_input_hooks_before_expansion`, `test_a_typed_template_line_a_hook_handled_starts_no_run`, `test_a_typed_template_line_the_hooks_turn_into_text_is_that_text`; `test_native_typed_input_hooks.py`: `test_a_typed_template_line_meets_the_input_hooks` (R3); `test_native_coding_session_controller.py`: `test_a_typed_unknown_skill_meets_the_hooks_and_goes_to_the_model`, `test_a_typed_line_a_hook_rewrites_into_an_unknown_skill_goes_as_rewritten`; `test_native_typed_input_hooks.py`: `test_a_typed_unknown_skill_meets_the_hooks_and_goes_to_the_model`; `test_native_repl_resources_pty.py`: `test_pty_unknown_skill_goes_to_the_model_as_typed` (T5) | Custom commands expand like templates, an unknown skill is reported on stderr before the line goes to the model as it stands, and a typed line that names no skill or template meets the `input` hooks when its turn is prepared. DEV-40. |
| SLH-10 | X1, N2 | closed | `test_native_automation_prompt_dispatch.py`: `test_print_mode_runs_mcp_and_starts_no_run`, `test_json_mode_runs_mcp_and_emits_only_the_header`, `test_a_prompt_an_input_hook_handled_starts_no_run`; `test_native_automation_rpc_prompt_dispatch.py`: `test_mcp_is_handled_and_starts_no_run`, `test_a_prompt_behind_a_run_is_queued`, `test_a_queued_prompt_is_never_answered_as_started`, `test_a_prompt_an_input_hook_handled_is_answered_handled`; `test_native_extension_input_hooks.py`: `test_a_handled_input_ends_the_chain`, `test_a_typed_line_an_input_hook_handled_starts_no_run` |  |
| SLH-11 | X1 | closed, with limits | `test_native_automation_rpc_ui.py`: `test_notify_writes_one_request_and_waits_for_nothing`, `test_select_returns_the_value_of_its_response`, `test_mcp_asks_the_client_which_server_to_reconnect`; `test_native_automation_prompt_dispatch.py`: `test_mcp_subcommand_runs_from_a_prompt`; `test_native_automation_rpc_extension_ui.py`: `test_an_extension_command_uses_the_client_as_its_ui`, `test_a_session_start_hook_has_the_client_as_its_ui`, `test_a_dialog_timeout_ends_it_with_its_default`, `test_dialogs_of_startup_and_transition_hooks_get_their_answers` (N2); `test_native_extension_dialog_timeout.py`: `test_the_countdown_shows_the_seconds_left_and_cancels_at_zero`, `test_a_terminal_dialog_ends_when_its_timeout_passes`, `test_the_terminal_driver_takes_the_timeout_of_an_extension_dialog`; `test_native_extension_custom_ui_pty.py`: `test_pty_extension_confirm_counts_its_timeout_down_and_expires` (R3) | A dialog has no `signal` option; startup keeps its 120 s limit. DEV-41. |
| SLH-12 | X1 | closed | `test_native_automation_rpc_ui.py`: `test_mcp_login_takes_the_pasted_redirect_url`, `test_mcp_login_through_the_browser_ends_the_input_request`, `test_mcp_login_is_cancelled_by_the_client_or_the_end_of_input` |  |
| SLH-13 | X1, N2, R3 | closed | `test_native_automation_rpc_queued_commands.py`: `test_queued_input_that_names_an_extension_command_is_refused`, `test_a_prompt_that_names_a_command_runs_at_once_during_a_run`, `test_a_failing_command_during_a_run_is_an_extension_error`, `test_queued_skills_and_templates_are_expanded`, `test_queued_text_with_a_leading_space_is_not_expanded`, `test_a_command_run_during_a_run_is_answered_before_the_session_ends`; `test_native_automation_rpc_prompt_dispatch.py`: `test_an_idle_queued_message_a_hook_handled_frees_the_session`; `test_native_automation_rpc_queued_commands.py`: `test_queued_input_meets_the_input_hooks_before_expansion`, `test_a_dialog_of_a_queued_input_hook_gets_its_answer`, `test_a_prompt_admitted_as_the_run_ends_is_expanded_once` (R3) |  |

### Interactive `/mcp` manager (MGR)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| MGR-01 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_servers_that_need_the_user_come_first_then_by_name`, `test_a_server_is_listed_with_state_exposure_and_source`; `test_native_mcp_commands.py`: `test_in_a_terminal_mcp_opens_the_manager_without_waiting`; `test_native_mcp_manager_pty.py`: `test_the_manager_disables_a_server_and_the_list_follows` |  |
| MGR-02 | S34 | closed | `test_native_mcp_manager_flow.py`: `test_the_manager_opens_at_once_and_follows_the_servers`; `test_native_mcp_manager_view.py`: `test_a_live_menu_follows_the_servers_and_keeps_the_selected_item`; `test_native_mcp_manager_menus.py`: `test_config_errors_are_shown_above_the_list`, `test_without_servers_the_list_says_where_to_add_them`; `test_native_mcp_manager_registered.py`: `test_status_and_menu_list_the_overridden_registrations`; `test_native_mcp_manager_view.py`: `test_page_keys_scroll_a_message_that_does_not_fit`, `test_a_long_error_scrolls_while_the_menu_keeps_its_keys` (N3) |  |
| MGR-03 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_a_server_shows_its_transport_source_and_state`, `test_the_last_actions_error_is_shown_before_the_servers`, `test_describe_transport_is_the_url_or_the_command_line`; `test_native_mcp_manager_view.py`: `test_a_long_error_leaves_the_actions_on_the_screen`, `test_page_keys_do_not_reach_the_input_of_the_sign_in_screen` (N3) |  |
| MGR-04 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_the_actions_depend_on_the_state`, `test_each_action_says_what_it_does_and_where_it_is_saved`, `test_enable_and_disable_say_where_the_change_is_saved` |  |
| MGR-05 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_a_user_level_server_can_be_changed_for_the_trusted_project_alone`; `test_native_mcp_manager_flow.py`: `test_a_change_for_the_project_is_saved_as_an_override` |  |
| MGR-06 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_tools_show_the_first_line_and_an_exposure_that_differs`; `test_native_mcp_manager_flow.py`: `test_tools_are_shown_until_a_key_and_then_the_server_again` |  |
| MGR-07 | S34 | closed | `test_native_mcp_manager_menus.py`: `test_exposure_offers_the_three_and_marks_the_current_one`; `test_native_mcp_manager_flow.py`: `test_choosing_an_exposure_saves_it_and_registers_the_tools_again` |  |
| MGR-08 | S34 | closed | `test_native_mcp_manager_flow.py`: `test_disabling_and_enabling_run_in_the_background`, `test_reconnect_runs_in_the_background_and_its_error_is_the_servers` |  |
| MGR-09 | S34, N3 | closed, with limits | `test_native_mcp_manager_flow.py`: `test_sign_in_shows_each_step_and_ends_at_the_servers_menu`, `test_cancel_at_the_sign_in_screen_and_while_connecting_stops_the_sign_in`; `test_native_mcp_manager_view.py`: `test_the_sign_in_screen_shows_the_url_an_input_and_the_keys`, `test_confirm_submits_a_pasted_url_that_is_not_empty`, `test_the_copy_key_copies_the_url_and_says_how_it_went`, `test_the_copy_key_is_the_users_app_message_copy_binding`, `test_the_url_and_the_click_hint_are_links_to_the_url`, `test_a_url_the_frame_cannot_link_is_plain_text`; `test_native_frame_hyperlinks.py`: `test_a_wrapped_link_is_closed_and_opened_again_on_every_line`, `test_finishing_a_full_frame_keeps_the_links_of_a_linked_row`; `test_native_mcp_login_pty.py`: `test_mcp_login_signs_in_through_the_browser`, `test_escape_cancels_a_login_that_waits_for_the_browser` | A URL that is not printable ASCII up to 4096 characters stays plain text without the click hint. DEV-43. |
| MGR-10 | S34 | closed | `test_native_mcp_manager_view.py`: `test_a_menu_shows_details_errors_items_and_what_the_keys_do`, `test_key_hints_wrap_in_a_narrow_terminal` |  |

### Rendering (RND)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| RND-01 | S35 | closed, with limits | `test_native_tool_rows.py`: `test_mcp_call_is_titled_server_tool_with_its_arguments`, `test_a_registered_mcp_tool_is_titled_with_its_exact_names`; `test_native_mcp_rows_pty.py`: `test_an_mcp_call_has_its_own_row_and_ctrl_o_expands_it` | DEV-44. |
| RND-02 | S35 | closed, with limits | `test_native_tool_rows.py`: `test_mcp_result_collapsed_shows_the_first_five_lines`, `test_mcp_result_counts_wrapped_lines_at_the_box_width`, `test_mcp_result_names_the_full_output_file_when_collapsed`, `test_mcp_error_result_uses_the_error_colour` | While a call runs the row shows the last progress line (Pi replaces the partial result). DEV-18. |
| RND-03 | S35 | closed, with limits | `test_native_session_history_render.py`: `test_restored_mcp_calls_get_mcp_rows_without_their_server`; `test_native_tool_rows.py`: `test_calls_to_mcp_tools_that_are_not_registered_get_mcp_rows`; `test_native_export_tool_rows.py`: `test_an_mcp_call_without_its_tool_is_drawn_like_in_the_transcript`, `test_an_exported_file_has_the_rows_and_the_page_shows_them` | Pipy's export page is not Pi's page; it gained Pi's `renderedTools`. `pipy --export` and `/share` run no extension code. DEV-44. |
| RND-04 | S35, N3 | closed, with limits | `test_native_nested_lifecycle.py`: `test_nested_mcp_calls_are_rows_of_the_codemode_row`; `test_native_tool_rows.py`: `test_codemode_result_drops_the_script_header`, `test_codemode_result_counts_wrapped_lines_and_names_the_full_output`, `test_codemode_result_is_empty_while_the_script_runs`; `test_native_export_nested_calls.py`: `test_a_result_with_nested_calls_shows_them`, `test_a_drawn_call_shows_its_nested_calls_in_its_box`, `test_malformed_calls_are_left_out_and_the_page_goes_on` | Shown under the tool's pipy name; the codemode call is the generic row. DEV-66. |

### Extension API (EXT)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| EXT-01 | S02, R2 | closed, with limits | `test_native_extension_tool_metadata.py`: `test_activation_accepts_every_metadata_field`, `test_activation_rejects_a_wrong_metadata_shape`; `test_native_extension_tools.py`: `test_a_pi_style_schema_keeps_the_extension_enabled`, `test_a_non_callable_prepare_arguments_disables_the_extension`, `test_the_model_calls_a_pi_style_tool_through_prepare_arguments`, `test_prepare_arguments_runs_once_per_call` (R2) | A `prepare_arguments` that raises names only the exception type. DEV-79. No `constrained_sampling` (DEV-85). |
| EXT-02 | S02, S04, R5, T2 | closed, with limits | `test_native_extension_tool_metadata.py`: `test_result_carries_structured_content_and_is_error`, `test_is_error_result_reaches_the_model_as_an_error`; `test_native_codemode_result_shapes.py`: `test_script_receives_structured_content_text_or_a_tool_error`; `test_native_agent_event_adapters.py`: `test_automation_projection_emits_pi_tool_result_objects` (R5); `test_native_agent_loop.py`: `test_a_batch_ends_the_run_only_when_every_result_terminates`, `test_a_blocked_call_terminates_and_result_hooks_keep_the_hint` (T2); `test_native_extension_tool_terminate_updates.py`: `test_a_batch_that_all_asks_to_terminate_runs_no_follow_up_turn`, `test_one_result_that_does_not_terminate_keeps_the_follow_up_turn`, `test_a_nested_result_that_terminates_ends_nothing`, `test_a_tool_publishes_partial_results_on_the_session_thread`, `test_a_nested_tools_partial_results_reach_its_caller_and_the_events` (T2) | DEV-59. A partial result from `ctx.on_update` carries its text and `details`, not its images (T2). |
| EXT-03 | S02, S17 | closed, with limits | `test_native_extension_tool_metadata.py`: `test_get_all_tools_reports_every_registered_tool`, `test_a_permission_hook_decides_by_annotations`; `test_native_tool_capabilities_mcp.py`: `test_get_all_tools_reports_the_metadata_of_mcp_tools` | DEV-25. |
| EXT-04 | S02, R2 | closed, with limits | `test_native_extension_tool_metadata.py`: `test_hidden_re_registration_withdraws_the_tool`, `test_hidden_re_registration_removes_the_tool_from_the_next_request`, `test_a_tool_registered_while_the_session_runs_takes_effect_at_once` (G9); `test_native_extension_tool_metadata.py`: `test_another_extensions_tool_name_keeps_the_first_registration`; `test_native_extension_tools.py`: `test_duplicate_tool_name_keeps_the_first_and_both_extensions`; `test_native_extension_live_tools.py`: `test_an_earlier_loaded_extension_takes_a_tool_name_over`; `test_native_extension_contribution_names.py`: `test_a_tool_name_an_earlier_extension_has_is_no_collision` (R2) | DEV-26. |
| EXT-05 | S02, S18 | closed, with limits | `test_native_extension_tool_metadata.py`: `test_codemode_reach_follows_the_script`; `test_native_mcp_readiness.py`: `test_an_extension_tool_waits_for_the_reach_it_declares`, `test_the_built_in_reaching_tools_declare_their_reach` | DEV-34. |
| EXT-06 | S36 | closed, with limits | `test_native_extension_mcp_servers.py`: `test_a_server_registered_while_extensions_load_connects_with_the_session`, `test_servers_registered_during_the_session_connect_and_disconnect`, `test_a_server_registered_during_the_session_activates_tool_search` | `codemode` / `tool_search` is activated for a server registered later when the next turn starts, not inside the registering call. DEV-15. A replaced session keeps the loaded extensions (DEV-84). |
| EXT-07 | S36 | closed, with limits | `test_native_mcp_registry.py`: `test_an_invalid_name_or_config_names_the_extension`, `test_a_name_another_extension_registered_is_refused`, `test_registering_a_name_again_replaces_the_own_registration_in_place`; `test_native_extension_mcp_servers.py`: `test_names_another_extension_registered_are_rejected`, `test_pending_extensions_settle_a_shared_name_when_they_are_finalized` | Extensions that load before the project is trusted do not see each other's registrations; the later one is disabled (`duplicate_mcp_server`) when they are finalized. One extension registering `my-server` and `my_server` is refused the second. DEV-46. |
| EXT-08 | S36 | closed | `test_native_mcp_registry.py`: `test_unregister_only_removes_the_own_registration`, `test_servers_are_listed_in_registration_order_as_copies`; `test_native_extension_mcp_servers.py`: `test_a_loaded_extension_registers_and_unregisters_in_the_registry` |  |
| EXT-09 | S36 | closed, with limits | `test_native_extension_mcp_servers.py`: `test_the_mcp_json_server_is_preferred_over_a_registered_one`, `test_mcp_lists_a_registered_server_like_a_configured_one`; `test_native_mcp_manager_registered.py`: `test_status_and_menu_list_the_overridden_registrations`; `test_native_mcp_manager.py`: `test_a_server_an_extension_registered_changes_for_the_session_only` | The owner shown is the extension's path label, not an absolute path. DEV-46. |
| EXT-10 | S36 | closed, with limits | `test_native_extension_mcp_servers.py`: `test_extensions_are_told_about_every_change_of_the_registrations`, `test_registered_servers_are_reported_when_nothing_connects_them`, `test_an_extension_that_handles_the_event_is_taken_to_connect_the_servers`; `test_native_mcp_idle_notices.py`: `test_idle_delivery_shows_the_notices_and_runs_no_hook` (N1) | The report is shown while the session waits for input; the hooks run where the session drains its notices and at turn start, not inside the registering call. DEV-15, DEV-47. |
| EXT-11 | S37, G2, R5, T1 | closed, with limits | `test_native_extension_tool_orchestration.py`: `test_a_tool_runs_other_tools_through_the_extension_api`, `test_a_nested_tool_runs_tools_itself`, `test_a_tool_calls_itself`, `test_calls_from_several_threads_run_at_the_same_time`, `test_a_nested_result_keeps_its_images`, `test_a_nested_call_has_a_signal_and_updates`, `test_nested_calls_of_a_worker_thread_run_on_the_session_thread`; `test_native_extension_tool_orchestration.py`: `test_live_output_of_another_thread_is_published_on_the_session_thread`; `test_native_nested_lifecycle.py`: `test_loop_to_live_transcript_and_all_projections` (R5); `test_native_nested_execution_mode.py`: `test_a_queued_call_starts_before_the_call_ahead_of_it_ends`, `test_a_withdrawn_queued_call_has_a_start_and_an_end` (T1) | Callable tools fixed when the model's call starts; no session-wide sequential setting (a tool's `execution_mode` is honoured since H3, `test_native_nested_execution_mode.py`); nested calls still running when the model's call returns are cancelled, a queued one with its start and end events (T1, `test_native_nested_execution_mode.py`); the outcome's `duration_ms` includes the hooks; no usage is added up (DEV-13). DEV-48. |
| EXT-12 | S37 | closed, with limits | `test_native_extension_tool_loadout.py`: `test_a_tool_presents_the_loadout_while_it_is_declared`, `test_the_changes_of_several_tools_merge_in_declared_order`, `test_a_failing_loadout_function_is_reported_once_and_changes_nothing`, `test_the_loadout_runs_once_per_capability_value`, `test_a_replaced_value_keeps_its_own_loadout_per_snapshot` (G9); `test_native_extension_tool_prompts.py`: `test_the_loadout_reports_exposure_namespace_and_guidelines` (G9) | The function is not called again before each request with unchanged tools. DEV-49. |
| EXT-13 | S37 | closed, with limits | `test_native_extension_mcp_replacement.py`: `test_another_extensions_tool_is_activated_when_it_reaches_other_tools`, `test_another_extensions_tool_named_codemode_is_not_activated`, `test_a_replacement_waits_for_servers_only_when_it_declares_a_reach`; `test_native_tool_capabilities.py`: `test_extension_replacement_of_a_builtin_is_callable_by_its_exposure` | No notice when an extension replaces `codemode` or `tool_search`. DEV-50. |
| EXT-14 | S37 | closed, with limits | `test_native_extension_mcp_replacement.py`: `test_an_extension_with_the_mcp_command_replaces_builtin_mcp`, `test_a_reload_turns_builtin_mcp_off_and_on_with_the_extension`; `test_native_mcp_session_command.py`: `test_an_extension_command_named_mcp_takes_the_place_of_the_builtin` | Only an extension command replaces MCP, not a tool or flag name of Pi's MCP extension. DEV-50. |
| EXT-15 | S35, R2 | closed, with limits | `test_native_extension_tool_renderer_resolver.py`: `test_resolvers_run_in_order_and_each_can_defer_to_the_next`, `test_a_resolver_draws_the_calls_of_a_tool_that_is_not_registered`, `test_a_session_draws_calls_with_a_registered_resolver`; `test_native_tool_render_context.py`: `test_a_live_row_tells_the_renderer_about_the_call`, `test_invalidate_draws_the_row_again` (G9); `test_native_extension_tool_renderer_resolver.py`: `test_a_resolver_wraps_a_built_in_tools_rows`, `test_a_resolver_wraps_an_mcp_tools_rows` (G9); `test_native_extension_tool_renderer_resolver.py`: `test_a_resolver_registered_from_session_start_draws_later_calls`; `test_native_extension_live_tools.py`: `test_resolvers_registered_late_join_their_extension_in_load_order` (R2); `test_native_session_replacement.py`: `test_a_resolver_from_session_start_is_asked_once_in_the_next_session`, `test_a_tool_and_a_server_registered_in_the_old_session_are_gone`; `test_native_extension_live_tools.py`: `test_another_session_keeps_only_what_the_extensions_registered_loading`; `test_native_mcp_registry.py`: `test_another_session_goes_back_to_what_the_extensions_loaded` (R7); `test_native_extension_tool_renderer_resolver.py`: `test_a_wrapped_edit_row_shows_the_live_preview`; `test_native_ui_coding_session_renderer.py`: `test_a_wrapped_bash_row_ticks_elapsed_until_the_result`, `test_a_bash_row_left_to_the_generic_renderers_does_not_tick` (r3 S11), `test_a_wrapped_bash_row_shows_the_streamed_output_once` (INT5) | `renderShell` is not ported. DEV-45. |
| EXT-16 | S20 | closed, with limits | `test_native_mcp_session_section.py`: `test_a_before_agent_start_hook_reads_and_changes_prompt_sections`, `test_a_hook_replaces_a_builtin_section_and_bad_entries_are_reported`; `test_native_extension_input_hooks.py`: `test_event_system_prompt_is_rendered_on_every_read` (G9) | Only `event.system_prompt_options.sections` exists; Pi's other option fields are not built. DEV-35. DEV-78. |

### SDK (SDK)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| SDK-01 | S38 | closed | `test_sdk_codemode_mcp.py`: `test_a_session_has_none_of_the_three_unless_asked`, `test_a_given_tool_stays_inactive_until_something_activates_it`, `test_the_settings_activate_the_tools_the_session_was_given` |  |
| SDK-02 | S38 | closed, with limits | `test_sdk_codemode_mcp.py`: `test_mcp_options_default_to_what_the_command_does`, `test_the_embedder_loads_the_configuration_and_builds_the_transports`, `test_the_server_log_goes_to_the_given_path`, `test_the_first_prompt_waits_as_long_as_the_embedder_says`, `test_the_credential_store_and_the_config_updater_reach_the_manager`, `test_the_url_opener_reaches_the_mcp_command`; `test_product_session_commands.py`: `test_submit_runs_an_extension_command_and_starts_no_run`, `test_submit_runs_the_mcp_command`, `test_a_failing_command_is_an_extension_error` (N2) | A product session has no UI: `/mcp` without a subcommand is a notice for the `diagnostic_sink`. DEV-51. |
| SDK-03 | S38 | closed, with limits | `test_sdk_codemode_mcp.py`: `test_codemode_options_replace_the_settings`, `test_a_session_presents_its_tools_as_the_codemode_options_say` | No `models` field (DEV-04). DEV-51. |
| SDK-04 | S38 | closed, with limits | `test_sdk_codemode_mcp_example.py`: `test_the_example_runs_a_prompt_with_all_three` | The example is offline: a scripted fake provider and an in-process MCP server. DEV-51. |

### Documentation (DOC)

| Row | Slice | State | Tests | Limits and deviations |
| --- | --- | --- | --- | --- |
| DOC-01 | each slice; S40 | closed | `test_mcp_parity_audit.py`: `test_the_user_page_has_a_section_for_every_topic_of_doc_01` |  |
| DOC-02 | S10, S19, S20a | closed | `test_mcp_parity_audit.py`: `test_the_cli_reference_covers_what_doc_02_names` |  |
| DOC-03 | S04, S07, S08 | closed | `test_mcp_parity_audit.py`: `test_the_codemode_page_covers_what_doc_03_names` |  |
| DOC-04 | S02, S36, S37 | closed | `test_mcp_parity_audit.py`: `test_the_extension_api_reference_covers_what_doc_04_names` |  |
| DOC-05 | each slice | closed, with limits | `test_mcp_parity_audit.py`: `test_the_changelog_names_the_user_visible_parts` | Checked by key terms; whether each entry is complete was read, not tested. |

## What this note does not show

- **Live use.** Plan section 6.5 asks, before the campaign closes, for one
  stdio server started through `uvx` or `npx` and one OAuth HTTP server,
  driven through `pipy mcp add`, `pipy mcp list`, `/mcp`, a codemode script
  and a `direct` call, with a real model. That was not done in this slice:
  it needs the owner's provider login and an account at an OAuth MCP
  server. The nearest evidence is the conformance suite (a third-party
  implementation of the server side, OAuth included) and the PTY tests of
  `/mcp`, the manager and the sign-in. Neither is daily use.
- **HTTPS.** No test connects over TLS; the HTTP tests use `http://127.0.0.1`.
  The transport uses Python's standard library for TLS.
- **Windows** (DEV-09; outside the parity goal by the owner's decision of
  2026-10-14, plan section 7, Q7).
- **A real browser.** Sign-in tests use a test double that follows the
  redirect. That the system browser opens is not tested.
- **Real provider requests with images.** The adapter tests check the
  request bodies against each family's format; no request was sent to a
  provider.
- **Load.** Many servers, large tool lists and long sessions were not
  measured.

## Open for the owner

1. The merge of `feat/mcp-parity` to `main` (Q5).
2. The live evidence of plan section 6.5.
3. Which of the DEV rows of plan section 3.1 that Q11 and Q12 do not name
   are accepted. Until then the rows that name one are closed on that
   condition.

Decided since the audit (plan section 7, 2026-10-14): no default Client ID
Metadata Document (Q1), the goal-check findings C7, O6 and M5 are outside
the parity goal (Q7), inline terminal images are in scope (Q8, built in N4),
and Pillow stays an optional extra (Q10). Decided 2026-10-15: the
findings of the second goal check that its triage neither fixes nor
refutes are accepted as the DEV rows Q11 names, and those of the third
goal check as the DEV rows Q12 names. SLH-13 and the hyperlink
part of MGR-09 were built (N2, N3), and the startup report is shown behind the
`/mcp` manager as it arises (N1).
