# MCP parity gaps, goal check r2: notes for the shared files

**Folded in INT4 (2026-10-15).** The merge of R2 and R5 into
`feat/mcp-parity` put this text into `CHANGELOG.md`, `docs/backlog.md`, the
plan, the acceptance note and `tests/test_mcp_parity_audit.py`. The proposed
row for a raising `prepare_arguments` is DEV-79, and the candidate text on
`ToolCallOutcome.duration_ms` went into DEV-48. This file stays as a record;
the shared files are the source of truth.

Slices of `handoff/triage-r2.md` ran on a parallel track that does not edit `CHANGELOG.md`, `docs/backlog.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them; a
later merge folds it in.

## R2: Extension tool registration (S18, S19, S20, S27)

### R2a: Pi-style input schemas and `prepare_arguments` (S18, S19)

**Changelog (Unreleased, Changed/Added):**

- An extension tool's input schema is now held to the rules of an MCP
  server's tool (`schema_mode="foreign"`) instead of pipy's strict subset, as
  Pi accepts any TypeBox schema: `number`, `null`, unions and `$ref` no longer
  disable the extension with `invalid_tool`. Arguments are converted and
  checked as an MCP tool's are; properties the schema does not declare are
  kept unless `additionalProperties` forbids them.
- `ExtensionTool(prepare_arguments=...)` (Pi `prepareArguments`): a callback
  that receives a call's parsed arguments before validation and returns the
  arguments to validate and run. If it raises, the call ends with the error
  result `<name>: prepare_arguments raised <exception type>`.

**Plan:**

- EXT-01 row: the evidence now includes `types.ts` `prepareArguments`
  (line 598) and `agent-loop.ts` `prepareToolCallArguments`.
- DEV-10, last sentence: replace "Native and extension tools keep pipy's
  strict subset, without coercion." with "Native tools keep pipy's strict
  subset, without coercion; extension tools are foreign-schema tools since
  goal check r2 (S18), prepared and validated as above."
- No DEV row is added or removed by R2a. The error text of a raising
  `prepare_arguments` names only the exception type, as an extension tool
  handler's error does (`extension tool error: <type>`); Pi shows the
  exception message. If the merge wants it recorded, extend the DEV row that
  covers the handler error text, or add: "EXT-01 | A `prepare_arguments` that
  raises gives `<name>: prepare_arguments raised <type>`, without the
  exception message Pi shows. | other form | Goal check r2 (S19)".

**Acceptance (EXT-01 row, add tests):**
`test_native_extension_tools.py`: `test_a_pi_style_schema_keeps_the_extension_enabled`,
`test_a_non_callable_prepare_arguments_disables_the_extension`,
`test_the_model_calls_a_pi_style_tool_through_prepare_arguments`.

**Audit test references:** none changed.

R2a also fixed (changelog, Fixed): a model call to an extension tool runs as
a parent that may call other tools, and that path prepared the call's
arguments a second time instead of running those the `tool_call` hooks saw.
The agent loop now hands the parent runner the prepared, hook-checked
arguments, as the direct path does.

Acceptance (EXT-01 row, add test): `test_native_extension_tools.py`:
`test_prepare_arguments_runs_once_per_call`.

### R2b: Tool name collisions and late tool renderers (S20, S27)

**Changelog (Unreleased, Changed):**

- When two extensions register the same tool name, the one that loaded first
  keeps it and the other extension stays enabled with its other
  contributions, as in Pi's `getAllRegisteredTools`; it was disabled with
  `duplicate_tool`, which is gone. This holds while the session runs too: a
  later-loaded extension's `register_tool` of that name is left out silently
  (it raised `ExtensionCapabilityError`), an earlier-loaded one's takes the
  name over.
- `api.register_tool_renderer` works while the session runs the extension
  (from `session_start`, a command, a hook or a tool), as Pi allows; it
  raised once loading had finished. The resolver joins its extension's other
  resolvers in load order and is asked for rows drawn from then on.

**Plan:**

- DEV-26: drop "A tool name another extension registered disables the
  second extension while loading and raises afterwards, where Pi lets the
  first one win silently." and change "the other `register_*` methods stay
  closed after loading" to "`register_tool_renderer` too (goal check r2,
  S27); the other `register_*` methods stay closed after loading".
- DEV-45: unchanged (S25/S26 stay accepted there).
- Plan note at line 1845 ("EXT-15. `register_tool_renderer` is staged like a
  hook"): add that since goal check r2 a registration after loading goes to
  the generation's live registry (`extensions/live_tools.py`) in extension
  load order.

**Acceptance:**

- EXT-04 row, add tests: `test_native_extension_tool_metadata.py`:
  `test_another_extensions_tool_name_keeps_the_first_registration`;
  `test_native_extension_tools.py`:
  `test_duplicate_tool_name_keeps_the_first_and_both_extensions`;
  `test_native_extension_live_tools.py`:
  `test_an_earlier_loaded_extension_takes_a_tool_name_over`;
  `test_native_extension_contribution_names.py`:
  `test_a_tool_name_an_earlier_extension_has_is_no_collision`.
- EXT-15 row, add tests: `test_native_extension_tool_renderer_resolver.py`:
  `test_a_resolver_registered_from_session_start_draws_later_calls`;
  `test_native_extension_live_tools.py`:
  `test_resolvers_registered_late_join_their_extension_in_load_order`.

**Audit test references:** none changed. A reference to the removed
`test_native_extension_tools.py::test_duplicate_tool_name_disables_second` or
`test_native_extension_tool_metadata.py::test_another_extensions_tool_name_is_still_a_duplicate`
in the acceptance note or the audit test must be renamed to the tests above.

## R5: Structured tool results in JSON/RPC events and progress (S29, D7, U6, S28)

**Changelog (Unreleased, Changed):**

- JSON and RPC tool events follow Pi's documented contract (`docs/json.md`):
  `tool_execution_end.result` and `tool_execution_update.partialResult` are
  now `{content, details}` objects instead of strings. `result.content` has
  the result's text and image blocks, as in the `toolResult` message,
  `result.details` the result's details when it has them, and
  `result.structuredContent` the tool's machine-readable output when it gave
  one. `tool_execution_end` carries `durationMs`, how long the tool itself
  ran (Pi times `execute()` alone, without the hooks), for nested calls
  (`parentToolCallId`) too; it is absent when the tool never started. A codemode call's `nestedCalls`
  record moved from a top-level `details` field of `tool_execution_end` into
  `result.details`. Clients that read `result` or `partialResult` as a
  string must read `content[].text` instead.
- An MCP tool's progress is a partial result `{content: [{type: "text",
  text}], details: {server, tool}}` that replaces the previous one, as in Pi;
  it was a `"<text>\n"` chunk. `bash` output updates are
  `{content: [{type: "text", text: <chunk>}], details: {}}`, each chunk
  continuing the text before it.
- `ctx.execute_tool(..., on_update=)` gives the callback a `ToolResult` per
  partial result (Pi `onUpdate`) instead of a text chunk: the MCP progress
  text with `details={server, tool}`, or the next chunk of `bash` output.
  The terminal row is unchanged (progress lines are appended, DEV-18).

**Changelog (Unreleased, Added):**

- The automation fake provider (`--native-provider fake --native-model
  fake-tools`) asks for one `bash` call (`echo hi`) when the prompt starts
  with `TOOLCALL`; the RPC conformance gate checks the tool events of that
  call (`json_mode_tool_events`).

**Plan:**

- DEV-18: replace the text with "In the terminal, progress of a running call
  is appended to the call's live output line by line, and the row shows the
  last line; Pi's row replaces the preliminary result. JSON/RPC events and
  `on_update` get Pi's partial result `{content, details: {server, tool}}`,
  which replaces the previous one (goal check r2, U6)."
- DEV-48: replace "and a partial result is a text chunk (G2)" with "and a
  partial result reaches `on_update` as a `ToolResult` whose `content` is
  its text; a `bash` partial result is the next chunk of output where Pi's
  is the output so far (goal check r2, S28)".
- DEV-59: drop the last sentence ("The JSON and RPC event
  `tool_execution_end` carries no structured content."); since goal check r2
  (S29) `result.structuredContent` carries it.
- Section 7, Q11: R5 closes S29, D7, U6 and S28 (FIX); D13 stays accepted
  under DEV-18 (terminal row only).

**Backlog (`docs/backlog.md`, TOOLS3b):**

- Replace "JSON/RPC `toolResult` messages and `tool_execution_end` carry no
  `details`; Pi's thrown tool errors store `details: {}`, pipy stores none;"
  with "JSON/RPC `toolResult` messages carry `details` only for codemode
  parents (`tool_execution_end.result` carries them for every tool since goal
  check r2); Pi's thrown tool errors store `details: {}`, pipy stores none;".

**Acceptance:**

- TLS-10 row: limits text becomes "DEV-10: arguments are checked with pipy's
  own validator for a named keyword set. In the terminal, progress is
  appended to the call's live output line by line; JSON/RPC events and
  `on_update` get Pi's replacing partial result with `{server, tool}`
  details. DEV-18." The test `test_progress_notifications_become_live_updates`
  now asserts the partial results; add `test_native_agent_event_adapters.py`:
  `test_automation_projection_emits_pi_tool_result_objects`.
- RND-02 row: unchanged.
- EXT-02 row: add `test_native_agent_event_adapters.py`:
  `test_automation_projection_emits_pi_tool_result_objects` (structured
  content in `tool_execution_end.result`).
- EXT-11 row: limits text, replace "partial results are text chunks" with
  "a `bash` partial result is the next chunk of output"; add
  `test_native_extension_tool_orchestration.py`:
  `test_live_output_of_another_thread_is_published_on_the_session_thread`
  (on_update gets partial results) and `test_native_nested_lifecycle.py`:
  `test_loop_to_live_transcript_and_all_projections` (nested end `result`
  object and `durationMs`).
- CMD-24 row: add `test_native_agent_event_adapters.py`:
  `test_automation_projection_emits_pi_tool_result_objects` (nested
  `tool_execution_end` with image blocks, details, structured content and
  `durationMs`) and `test_native_automation_json_mode.py`:
  `test_json_mode_tool_events_carry_pi_result_objects`.

**Audit test references:** none changed. One test was renamed:
`test_native_nested_record.py::test_ordinary_result_details_keep_legacy_message_and_event_shape`
is now `test_result_details_reach_events_and_only_codemode_messages`
(no reference to it exists in the acceptance note or the audit test).

**Left open (candidate DEV text, EXT-11):** `ctx.execute_tool`'s
`ToolCallOutcome.duration_ms` still measures the nested call from its start
to its outcome, hooks included; Pi's `AgentToolCallOutcome.durationMs` is
the tool's `execute()` alone. The JSON/RPC `durationMs` already follows Pi.

**Gates:** `automation_rpc_conformance.py` gains `json_mode_tool_events`.
`automation_pi_comparison.py` was not extended: it cannot run in this
environment (the Pi checkout lacks the generated
`packages/ai/src/providers/data/azure.json`, so the Pi driver fails before
this change too), and a tool flow there could not be verified.
