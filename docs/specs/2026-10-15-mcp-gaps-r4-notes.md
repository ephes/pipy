# MCP parity gaps, goal check r4: notes for the shared files

Slices of `handoff/triage-r4.md` that run on a parallel track do not edit
`CHANGELOG.md`, `docs/backlog.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them; a
later merge folds it in. Links here point from this directory
(`../extension-api.md`); in `CHANGELOG.md` they start with `docs/`.

## V3: Tool rows, renderers, `/share`, extension tool errors (D2, D3, D4, S7, S5 doc)

### S7: a raising extension tool keeps its message

**Changelog (Unreleased, Fixed):**

- An extension tool whose handler raises fails the call with the
  exception's message as its error result, as Pi's agent loop reports
  `error.message` (Pi's docs tell extension authors to throw to fail a
  call); the type name stands in for an empty message. Characters that
  would steer a terminal become spaces (line breaks and tabs stay) and the
  text is bounded like other tool output. A caller through
  `ctx.execute_tool` or a codemode script gets the same text. Before, the
  result was only `extension tool error: <exception type>`. A raising
  `prepare_arguments` reports its message the same way, without the tool
  name (before: `<name>: prepare_arguments raised <exception type>`). See
  [extension API](../extension-api.md).

**Plan:**

- DEV-79: closed. Replace the row by
  "| DEV-79 (closed by V3) | EXT-01 | Was: a `prepare_arguments` that raises
  gave the error result `<name>: prepare_arguments raised <exception type>`,
  without the exception message Pi shows, as an extension tool handler's
  error named only its type. Since goal check r4 (S7, V3) both give the
  exception's message, printable and bounded (the type name when it is
  empty), as Pi's `createErrorToolResult(error.message)`. | closed | Goal
  check r2 (S19, R2); recorded in INT4, accepted in Q11; closed in V3 |".
  Keep the DEV-79 mentions in Q11 (history).
- Section 3.1 lead paragraph (near "INT4 merged R2 and R5; DEV-79 (the error
  text of a raising `prepare_arguments`, accepted in Q11) set EXT-01 to
  closed with a limit."): add "V3 closed DEV-79 (goal check r4, S7); EXT-01
  keeps its limit through DEV-85."

**Acceptance (EXT-01 row):** remove "A `prepare_arguments` that raises
names only the exception type. DEV-79." from the note, so it reads "No
`constrained_sampling` (DEV-85)." Add tests:
`test_native_extension_tool_errors.py`:
`test_a_raising_handler_fails_the_call_with_its_message`,
`test_a_long_message_is_bounded`,
`test_a_raising_prepare_arguments_reports_its_message`,
`test_a_prepare_arguments_without_a_message_reports_its_type`;
`test_native_extension_tools.py`:
`test_extension_tool_exception_fails_the_call_with_its_message` (renamed
from `test_extension_tool_exception_is_bounded`, which no shared file
names). `test_the_model_calls_a_pi_style_tool_through_prepare_arguments`
now expects the message.

**Audit test references:** `test_q11_accepts_the_rows_of_the_triage` keeps
passing (Q11 still names DEV-79, and the closed row's source column says
"accepted in Q11"). If the merge adds a check that the EXT-01 note no
longer names DEV-79, it belongs next to
`test_the_fixes_of_the_third_triage_left_the_limits`.

### D2: `render_call` knows a failed call

**Changelog (Unreleased, Fixed):**

- A tool renderer's call context has `is_error` set once the call failed,
  as Pi takes `isError` from the call's current result for both phases, so
  a custom `render_call` drawn again after the result in the terminal
  (Ctrl+O, a resize, `invalidate()`) can mark the failure. Before, the call
  context always said `False`. The HTML export draws a call before its
  result, with `is_error` false, as Pi's export does. See
  [tool rendering](../extension-api.md#tool-rendering).

**Plan:** RND-01 / EXT-15 notes need no change; no DEV row named this.

**Acceptance (EXT-15 row, add tests):** `test_native_tool_render_context.py`:
`test_the_call_drawn_again_after_a_failure_knows_it_failed`;
`test_native_export_tool_rows.py`:
`test_an_exported_call_is_drawn_before_its_result` (RND-03: the export's
call context, as Pi's).

**Audit test references:** none changed.

### D3: a resolver can wrap codemode's rows

**Changelog (Unreleased, Fixed):**

- A tool renderer resolver's `next()` gives renderers for the calls to
  `codemode`, as Pi's codemode tool brings its `codemodeRenderers`: they
  draw the rows pipy draws for codemode (in the terminal and the HTML
  export the script output without the `Script completed` header), so a
  resolver can wrap them there and in captured output. Before, `next()` gave `None`
  for `codemode`, and a resolver that returned `None` without `next()` left
  codemode's own result rows in place instead of the generic row. An
  extension tool named `codemode` that has renderers keeps them. See
  [tool rendering](../extension-api.md#tool-rendering).

**Plan:**

- DEV-45: after "pipy's own rows are callable renderers a resolver can wrap
  (since G9)" add "(also `codemode`'s since goal check r4, D3)". Add at the
  end: "An extension tool named `codemode` without renderers gets pipy's
  codemode rows from `next()` and without a resolver; Pi draws it with the
  generic rows."

**Acceptance (EXT-15 row, add tests):**
`test_native_extension_tool_renderer_resolver.py`:
`test_next_gives_codemodes_own_rows`, `test_a_resolver_wraps_codemodes_rows`,
`test_a_resolver_that_chooses_nothing_for_codemode_gets_the_generic_row`,
`test_the_line_renderer_lets_a_resolver_wrap_codemodes_rows`;
`test_native_export_tool_rows.py`:
`test_a_resolver_wraps_codemodes_rows_in_the_export`. The row note keeps
ending with "`renderShell` is not ported. DEV-45." (the audit test
`test_the_fixes_of_the_third_triage_left_the_limits` checks that ending).

**Audit test references:** none changed.

### D4: `/share` uploads the page `/export` writes

**Changelog (Unreleased, Fixed):**

- `/share` uploads the page `/export` writes, as Pi's `/share` uploads its
  `exportToHtml` page: the session's system prompt, and the tool calls drawn
  with the loaded extensions' renderers and resolvers and the session's MCP
  tool titles. Before, the shared page had no system prompt and drew only MCP
  calls, titled from their pipy names, so custom MCP renderers and resolvers
  were lost. The page is drawn on the session's thread before the upload
  starts, so no extension code runs on the upload's thread. `pipy --export`
  still runs no extension code. See
  [export](../export-distribution.md#artifact-shape).

**Plan:**

- DEV-44: replace "and `pipy --export` and `/share` run no extension code"
  with "and `pipy --export` runs no extension code (`/share` draws its page
  with the session's renderers on the session's thread before the upload,
  since goal check r4, D4)".
**Acceptance (RND-03 row):** in the note replace "`pipy --export` and
`/share` run no extension code." by "`pipy --export` runs no extension code;
`/share` draws with the session's renderers like `/export` (r4 D4)." Add tests: `test_native_export_tool_rows.py`:
`test_the_share_command_asks_the_loaded_extensions`,
`test_share_draws_the_page_before_the_upload_thread_starts`,
`test_share_session_html_has_the_prompt_and_the_drawn_calls`;
`test_native_export_distribution.py`:
`test_tool_loop_share_command_uses_token_and_share_boundary` (now also
checks the system prompt).

**Audit test references:** none changed.

### S5: `send_user_message` options (doc sentence)

**Changelog:** none (documentation of an accepted deviation).

**Plan:** DEV-90 (Q13) as written in `handoff/triage-r4.md` section 3b. The
docs sentence it needs is in `docs/extension-api.md`, after the
`api.on(...)` paragraph that follows the API listing.

**Acceptance:** none.
