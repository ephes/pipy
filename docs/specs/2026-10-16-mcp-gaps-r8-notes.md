# MCP parity gaps, goal check r8: notes for the shared files

Slices K2 and K3 of `handoff/triage-r8.md` run on the parallel track T9B
(branch `feat/mcp-parity-gaps-r8`), which does not edit `CHANGELOG.md`,
`docs/backlog.md`, the plan (`docs/specs/2026-10-12-mcp-parity-plan.md`),
the acceptance note (`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them;
the merge INT9 folds it in.

Links in this file are relative to `docs/specs`. When a changelog line is
copied into `CHANGELOG.md`, its links must start with `docs/` instead
(`../mcp.md#x` becomes `docs/mcp.md#x`). No DEV numbers are allocated
here; a new deviation would be written `DEV-new` for the merge to number.

## K2: OAuth discovery bodies (C) and config inherit (A)

### C: discovery does not wait for the body of a miss, an error or a redirect

**Changelog (Unreleased, Fixed):**

- OAuth discovery no longer waits for the body of an answer it does not
  use, as Pi's discovery cancels it: a 404, 502 or other failure status of
  a metadata URL is acted on at its headers, so a server whose error body
  stalls no longer holds the sign-in up until the request's time limit (and
  then fails it). The OAuth fetch also follows a redirect without waiting
  for the redirect's body, as Node's `fetch` does, also for registration and
  token requests. A fetch passed to the OAuth library
  (`adapt_oauth_provider(provider, fetch=...)`) takes the new
  `discard_unless_ok` argument. See
  [the OAuth library](../mcp.md#the-oauth-library).

**Plan:**

- OAU-02 row: after "A network failure fails the flow; any other failure
  means no metadata." add "The body of a miss, a failure status and a
  redirect is not waited for (Pi's `discard`; since goal check r8, C)." In
  its slice column add "goal check r8 (K2)".
- OAU-03 row: after "4xx and 502 try the next URL; another failure status
  is an error;" add "neither body is waited for (goal check r8, C);". In its
  slice column add "goal check r8 (K2)".
- DEV-72: add "A body that is not read is not limited: the body of a
  redirect that is followed, and in discovery the body of an answer that is
  not 2xx, are not read since goal check r8 (C), as `fetch` does not wait
  for the first and Pi's discovery cancels the second."
- Section 2.x text of S27 or L2 where the OAuth fetch is described as
  "returns the whole response" (`mcp/oauth/fetch.py`), if the plan has such
  a sentence: add "With `discard_unless_ok=True` (discovery's requests) a
  response that is not 2xx is returned at its headers with an empty body
  and its connection closed; a redirect that is followed is never read
  (goal check r8, C)."

**Acceptance:**

- OAU-02 row: add tests `test_native_mcp_oauth_discovery.py`:
  `test_a_stalled_body_of_a_miss_does_not_hold_discovery_up`,
  `test_a_stalled_body_of_a_failure_status_does_not_hold_discovery_up`;
  `test_native_mcp_oauth_fetch.py`:
  `test_with_discard_unless_ok_the_body_of_a_failure_is_not_waited_for`,
  `test_with_discard_unless_ok_a_2xx_body_is_read`,
  `test_the_body_of_a_redirect_that_is_followed_is_not_waited_for` (r8 C;
  `test_every_request_goes_through_the_given_fetch` now also asserts the
  argument).
- OAU-03 row: add tests `test_native_mcp_oauth_discovery.py`:
  `test_a_stalled_body_of_a_miss_does_not_hold_discovery_up`,
  `test_a_stalled_body_of_a_failure_status_does_not_hold_discovery_up`
  (r8 C).

**Audit test references:** none changed.

### A: `pipy config inherit builtin <name>`

**Changelog (Unreleased, Added):**

- `pipy config inherit builtin <name>` puts a project's setting for a
  built-in extension (`mcp`, `codemode`, `tool-search`) back to inherit, as
  the inherit state of Pi's config list does: it removes the `+`, `-` and
  `!` entries of `.pipy/settings.json` that name `builtin:<name>`, and a
  bare `builtin:<name>`, so the user settings decide again. A `!` glob that
  only matches it, such as `!builtin:*`, stays. `inherit` always writes the
  project settings (`--scope global` is refused) and needs a trusted
  project. Before, a project's entry could only be flipped between `+` and
  `-`. See [turning MCP off](../mcp.md#turning-mcp-off).

**Plan:**

- DEV-32: replace "the toggle is `pipy config enable|disable builtin
  <mcp|codemode|tool-search>`" with "the toggle is `pipy config
  enable|disable builtin <mcp|codemode|tool-search>`, and `pipy config
  inherit builtin <name>` puts a project's setting back to inherit, as the
  inherit state of Pi's config list (since goal check r8, A). `pipy config
  list` shows the effective state, not Pi's `project load` / `project
  unload` / `inherited global` labels. Inherit drops a project entry
  written `+./builtin:<name>` (or with `-` or `!`) too, since pipy's
  matching reads it as naming the built-in; Pi's selector compares the
  entry as written and keeps it."
- EXP-12 and EXP-13 rows: in the slice column add "goal check r8 (K2)". The
  row text needs no change.

**Acceptance:**

- EXP-12 row: add tests `test_native_mcp_discovery.py`:
  `test_config_command_puts_a_project_s_builtin_entry_back_to_inherit`,
  `test_config_command_inherit_writes_no_settings_it_cannot` (r8 A). In the
  limits column replace "`pipy config enable\|disable builtin mcp`" with
  "`pipy config enable\|disable\|inherit builtin mcp`".
- EXP-13 row: add test `test_native_mcp_discovery.py`:
  `test_config_command_puts_a_project_s_builtin_entry_back_to_inherit` (r8
  A, `tool-search`). In the limits column replace "`pipy config
  enable\|disable builtin <name>`" with "`pipy config
  enable\|disable\|inherit builtin <name>`".

**Audit test references:** none changed.

## K3: top-level nestedCalls (B), nested argument preview (D1), "Tool X not found" (U5)

### B: JSON/RPC tool result messages carry `nestedCalls` at the top level

Pi checked (`packages/ai/src/types.ts` `ToolResultMessage`,
`agent-session.ts` `_handleAgentEvent`, `docs/message-types.md`): the
record is the message's own `nestedCalls` field, and the tool's `details`
never hold it (codemode's are `{calls, fullOutputPath?}`). So pipy's
message drops it from `details` too: a message with a record has
`nestedCalls`, plus the rest of its `details` when there are any. The
`result` of `tool_execution_end` still has the record in
`details.nestedCalls`, as before (Pi's `result` has none; pipy's session
keeps the record there). pipy emits no `message_start`/`message_end` for
tool results (a gap outside this item), so the field shows in `turn_end`,
`agent_end` and `get_messages`.

**Changelog (Unreleased, Fixed):**

- In JSON and RPC mode, the `toolResult` message of a tool that ran other
  tools (a `codemode` script, or an extension tool through
  `ctx.execute_tool`) carries the record of those calls as its own
  `nestedCalls` field, as Pi's `ToolResultMessage` does, in `turn_end`,
  `agent_end` and `get_messages`. Before, it was inside the message's
  `details`, which no longer hold it; the message keeps the rest of the
  result's details. `tool_execution_end` still has the record in its
  `result.details`. See
  [the event vocabulary](../automation-rpc.md#event-vocabulary-pipy-must-emit).

**Plan:**

- CMD-26 row: in the slice column add "goal check r8 (K3)". In section 2.x
  where the JSON/RPC form of the record is described (the text that the
  4b12ab18 slice added: "pipy inside `details`"), replace it with "the
  message's own `nestedCalls` field, as Pi's `ToolResultMessage`; the
  message's `details` do not hold it, `tool_execution_end.result.details`
  still do (goal check r8, B)."
- Backlog TOOLS3b bullet "JSON/RPC `toolResult` messages carry `details`
  only for codemode parents": replace "only for codemode parents" with
  "only for the parents of nested calls (minus the record, which is the
  message's `nestedCalls` field since goal check r8, B)".

**Acceptance:**

- CMD-26 row: add tests `test_native_nested_record.py`:
  `test_result_details_reach_events_and_only_messages_with_a_record`,
  `test_parent_only_history_and_coherent_live_duration`;
  `test_native_extension_tool_orchestration.py`:
  `test_rpc_tool_result_messages_carry_an_extension_parents_record` (r8 B).
- EXT-11 row, if it names
  `test_rpc_tool_result_messages_carry_an_extension_parents_record`: the
  test now reads the message's `nestedCalls` field; no text change needed.

**Audit test references:** none changed.

### D1: the start of left-out arguments survives a resume and the export

**Changelog (Unreleased, Fixed):**

- A codemode call's nested call whose arguments are over 8 KiB (or past
  the record's 32 KiB in all) shows the first 200 characters of them in a
  resumed session's row and in the HTML export, as Pi's codemode rows show
  each call's `args` preview, instead of only `[<n> argument bytes]`: the
  codemode result keeps them in `details.argumentPreviews` beside the
  `nestedCalls` record, whose arguments stay bounded. See
  [codemode](../codemode.md#nested-evidence-and-availability).
- The export's credential redaction withholds a quoted value that the text
  ends inside to the end of the text: a 200-character argument preview (or
  an error cut to its bound) that stops inside a password no longer shows
  the rest of it after the first word. See
  [the export](../export-distribution.md).

**Plan:**

- RND-04 row: in the slice column add "goal check r8 (K3)".
- DEV-96: after "where Pi's draws the `details.calls` it saved with the
  result." add "For a call whose arguments the record leaves out, the row
  shows the first 200 characters of them from the result's
  `details.argumentPreviews` (Pi's `ARGS_PREVIEW_CHARS`), taken from the
  arguments' JSON as the script sent it where Pi stringifies the parsed
  arguments (goal check r8, D1)."
- DEV-66: after "does not draw codemode calls ahead (no codemode call
  renderer)" add "; so its nested-call lines show the preview of arguments
  the record left out before `[arguments omitted, <n> bytes]`, where Pi's
  page shows the preview in codemode's row and the marker alone in
  `renderNestedCalls` (goal check r8, D1)".

**Acceptance:**

- RND-04 row: add tests `test_native_nested_record.py`:
  `test_left_out_arguments_keep_a_preview_through_a_resume`,
  `test_malformed_previews_fall_back_to_the_byte_count`;
  `test_native_codemode_composite.py`:
  `test_a_call_whose_arguments_the_record_leaves_out_keeps_a_preview`;
  `test_native_export_nested_calls.py`:
  `test_left_out_arguments_show_their_preview`;
  `test_native_export_distribution.py`:
  `test_exports_redact_a_cut_short_argument_preview`,
  `test_redaction_covers_a_quoted_value_the_text_cuts_short` (r8 D1).
- CMD-26 row: add test `test_native_codemode_composite.py`:
  `test_a_call_whose_arguments_the_record_leaves_out_keeps_a_preview` (r8
  D1).

**Audit test references:** none changed.

### U5: a call to an undeclared tool is answered `Tool <name> not found`

**Changelog (Unreleased, Fixed):**

- A call to a tool that is not declared, or not callable from a codemode
  script or `ctx.execute_tool`, is answered `Tool <name> not found`, Pi's
  text (`agent-loop.ts` `prepareToolCall`), instead of
  `unknown tool: <name>`. See
  [tool exposure](../extension-api.md#tool-model).

**Plan:**

- EXP-03 row: replace "is rejected as an unknown tool." with "is rejected
  as an unknown tool, with Pi's text `Tool <name> not found` (goal check
  r8, U5)." In its slice column add "goal check r8 (K3)".

**Acceptance:**

- EXP-03 row: the test
  `test_native_codemode_extension_tools.py`:
  `test_direct_model_call_to_a_codemode_only_tool_is_an_unknown_tool` now
  asserts `Tool lookup not found`; add tests `test_native_mcp_session.py`:
  `test_codemode_only_mcp_tools_are_called_from_scripts_and_hidden_from_the_model`
  (r8 U5, `Tool mcp__docs__search not found`).

**Audit test references:** none changed.
