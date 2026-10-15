# MCP parity gaps, goal check r2: notes for the shared files

Slice R2 of `handoff/triage-r2.md` (extension tool registration) ran on a
parallel track that does not edit `CHANGELOG.md`, `docs/backlog.md`, the plan
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
