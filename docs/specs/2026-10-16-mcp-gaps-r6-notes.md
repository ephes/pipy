# MCP parity gaps, goal check r6: notes for the shared files

Slices of `handoff/triage-r6.md` on track T7B (`feat/mcp-parity-gaps-r6`) do
not edit `CHANGELOG.md`, `docs/backlog.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them; a
later merge (INT8) folds it in.

Links in this file are relative to `docs/specs`. In `CHANGELOG.md`, at the
repository root, the links of the changelog text must start with `docs/`
instead of `../` (for example `docs/extension-api.md#tool-rendering`).

## Y3: Rendering through wrapped and replaced renderers, and in the export (D1, D2, D3)

Findings D1, D2 and D3 of `handoff/goal-r6.md`. Rows: EXT-15, RND-03,
RND-04. No new DEV number; DEV-66 changes its wording (below).

What changed:

- D1: `ToolRenderContext._own_rows` is now `(ctx, phase, mcp_label) ->
  lines`: `BuiltinToolRows` and `McpToolRows` pass the context they are
  called with, and pipy's own rows (`tool_rows._own_lines` in the terminal
  and the export, `_ToolLoopRenderer._own_lines` in captured output) draw
  its `args`, `content`, `details`, `is_error`, `is_partial` and
  `expanded`, as Pi's built-in, MCP (`createMcpToolRenderers`) and codemode
  renderers draw what they are given. Limits: the `edit` diff preview is
  computed once from the call's own arguments (off the paint thread) and is
  left out under other arguments, where Pi computes a preview for the new
  arguments; the call part of a wrapped `edit` row shows the diff of the
  row's own result, where Pi's edit `renderResult` updates the call box from
  the result it was given; captured output is always collapsed, so
  `expanded` does not change it (there `content` None is no output, and a
  partial codemode result has no output, as Pi's codemode renderer).
- D2: the nested calls are part of codemode's result rows
  (`tool_rows._nested_rows`, drawn by the codemode branch of
  `_other_result_rows`), as Pi's `codemodeRenderers.renderResult` draws
  `details.calls`. `render_tool_row` no longer adds them for every row
  named `codemode`: renderers that replace codemode's (a resolver's own,
  or the generic row a resolver chose) draw none of pipy's. While the
  script runs, a row with nested calls has a partial result (empty
  content), as Pi's codemode tool publishes its calls with `onUpdate`, so
  a wrapper's `render_result` gets them. Own rows of a call that keeps no
  live calls (the export), or under other `details`, draw the calls of the
  details' `nestedCalls` record. Captured output writes the calls as they
  run only for codemode's own rows; a wrapper gets them with the result,
  from `details`. The export page's own "Nested calls" block
  (`renderNestedCalls`, Pi `template.js`) is unchanged.
- D3: the HTML export tells renderers `execution_started=True`
  (`RowRenderInputs.exported`), as Pi's export-html `tool-renderer.ts`
  renders with `executionStarted: true, argsComplete: true`;
  `args_complete` was already true.

**Changelog (Unreleased, Fixed):**

- pipy's own rows that a tool renderer resolver wraps (`next()`'s
  renderers for a built-in tool, `codemode` or an MCP tool) draw the
  context they are called with: its `args`, `content`, `details`,
  `is_error`, `is_partial` and `expanded`, as Pi's renderers draw the
  arguments, result and options they are given. A wrapper that passes
  `dataclasses.replace(ctx, args=..., content=...)` can now change or
  withhold what they show; before, they drew the call's own arguments and
  result whatever the context held. The `edit` diff preview is left out
  under other arguments. See
  [tool rendering](../extension-api.md#tool-rendering).
- A codemode script's nested calls are part of codemode's result rows, as
  in Pi's codemode renderer: renderers that replace codemode's, or the
  generic row a resolver chose, no longer show pipy's nested-call lines,
  and a resolver that wraps codemode's rows gets them in the result part,
  also while the script runs (its `render_result` is called with empty
  `content` once the script made a call). Captured output writes them as
  they run only for codemode's own rows. The export page's "Nested calls"
  section is unchanged. See [codemode](../codemode.md) and
  [tool rendering](../extension-api.md#tool-rendering).
- Renderers drawing a call for the HTML export (`/export`, `/share`) are
  told `execution_started=True`, as Pi's export renders every call as
  started; before it was false, so a renderer that waits for execution drew
  the call as not started.

**Plan:**

- Section 2.17, the S35 text "RND-04 needed no code: nested calls are rows
  of the `codemode` row ...": add "Since Y3 (goal check r6, D2) the nested
  calls are drawn by codemode's result rows (`tool_rows._nested_rows` in
  the codemode branch of `_other_result_rows`), as Pi's
  `codemodeRenderers.renderResult`: renderers that replace codemode's draw
  none, a wrapper of codemode's rows gets them in the result part, and a
  running row with nested calls has a partial result with empty content,
  as Pi's codemode tool publishes its calls with `onUpdate`. Captured
  output writes them as they run only for codemode's own rows."
- S35 Part 4 (`ToolRenderContext` fields): after "Captured rows ... and the
  HTML export draw once, so their `invalidate` does nothing" add "The HTML
  export tells renderers `execution_started=True` for every call
  (`RowRenderInputs.exported`), as Pi's export-html `tool-renderer.ts`
  (Y3, goal check r6, D3)."
- S35 Part 5: `_own_rows(phase, mcp_label)` becomes `_own_rows(ctx, phase,
  mcp_label)`; add "Since Y3 (goal check r6, D1) the rows draw the context
  they are called with (its `args`, `content`, `details`, `is_error`,
  `is_partial` and `expanded`), as Pi's renderers draw what they are given;
  the `edit` preview computed from the call's own arguments is left out
  under other arguments, and the call part of a wrapped `edit` row shows
  the row's own result's diff."
- DEV-66 (RND-04): "nested calls are drawn between the call and the output
  without Pi's spacer" becomes "nested calls are drawn at the start of the
  result part without Pi's spacer before them". The rest stays.
- Section 7: record Y3 under round 6 (Q15, written by INT8): D1, D2 and D3
  fixed, nothing accepted.

**Acceptance note:**

- EXT-15: add (r6 D1, D2) `test_native_extension_tool_renderer_resolver.py`:
  `test_a_wrapper_redacts_an_mcp_rows_arguments_and_result`,
  `test_a_wrapper_chooses_the_expansion_of_own_rows`,
  `test_a_wrapper_changes_a_built_in_tools_rows`,
  `test_a_wrapper_redacts_codemodes_rows`,
  `test_the_line_renderer_draws_what_a_wrapper_passes`. Limits column: add
  "Under other arguments a wrapped `edit` row has no diff preview."
- RND-04: add (r6 D2) `test_native_extension_tool_renderer_resolver.py`:
  `test_renderers_that_replace_codemodes_draw_no_nested_calls`,
  `test_codemodes_own_rows_draw_the_nested_calls_in_their_result`,
  `test_the_line_renderer_writes_nested_calls_only_for_codemodes_own_rows`;
  `test_native_export_tool_rows.py`:
  `test_wrapped_codemode_rows_draw_the_recorded_nested_calls`.
- RND-03: add (r6 D3) `test_native_export_tool_rows.py`:
  `test_export_renderers_are_told_each_calls_id` (now also checks
  `execution_started` and `args_complete`).

**Audit test (`tests/test_mcp_parity_audit.py`):**

- If INT8 adds a check for round 6 like
  `test_the_fixes_of_the_fifth_triage_ordered_and_drew_as_pi`, it can pin
  that the EXT-15 line names `test_a_wrapper_redacts_an_mcp_rows_arguments_and_result`
  and the RND-04 line names
  `test_renderers_that_replace_codemodes_draw_no_nested_calls`.
