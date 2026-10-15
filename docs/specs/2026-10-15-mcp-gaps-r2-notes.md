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
