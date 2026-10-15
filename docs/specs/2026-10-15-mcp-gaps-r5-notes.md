# MCP parity gaps, goal check r5: notes for the shared files

Slices of `handoff/triage-r5.md` on track T6B (`feat/mcp-parity-gaps-r5`)
do not edit `CHANGELOG.md`, `docs/backlog.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) or
`tests/test_mcp_parity_audit.py`. This file holds the text meant for them;
INT7 folds it in. Links here are relative to `docs/specs`; in
`CHANGELOG.md` they must start with `docs/` (`../mcp.md` becomes
`docs/mcp.md`).

## X2: MCP tool order on refresh (U2) and rendererless codemode replacements (S1)

### U2: a refreshed tool list keeps the tools' order

**Changelog (Unreleased, Fixed):**

- An MCP server's tools keep the order they were registered in when the
  server announces a new tool list, as Pi keeps the place of a tool it
  registers again: a list that orders them another way, or drops a tool
  and offers it again, leaves them where they were, and a new tool comes
  after the server's others. Before, the new list's order replaced it, so
  of two tools that match a `tool_search` query equally well the other one
  could come first after the change (see
  [MCP](../mcp.md#when-tools-change)).

**Plan:**

- TSR-07 (or the block for the r5 slices): "Since goal check r5 (U2) the
  manager keeps each server's tools in the order their names were first
  published (`McpManager._order`, never shrunk, as Pi's tool `Map` never
  drops a key), so a refreshed list that reorders, drops or restores tools
  leaves the tie order of the ranking as it was; a new tool comes after the
  server's others."
- New deviation row (next free number, DEV-94 unless T6A takes it), rows
  TSR-07, SES-02: "The MCP tools are ordered by server, the servers in
  configuration order, and within a server in the order the names were
  first published; the resource tools come after them. Pi keeps every MCP
  tool in one `Map` in the order of first registration, so the servers
  follow each other in the order they connected and a server's new tool
  comes after every tool registered before it, also other servers'. Only
  ties in `tool_search` across servers and the order of the declared tools
  differ; pipy's order does not depend on connection timing. | other form |
  Decided in X2 (goal check r5, U2)". (The order across servers was
  already configuration order since S15; X2 records it.)

**Acceptance:**

- TSR-07 row: add `test_native_mcp_manager.py`:
  `test_a_refreshed_list_keeps_the_order_the_tools_were_registered_in` (r5
  X2), and name the new DEV row in its limits.

**Audit test (`tests/test_mcp_parity_audit.py`):** if TSR-07's
acceptance row is checked there, add the new test name and the DEV row.

### S1: an extension tool without renderers keeps its registration in the renderer lookup

**Changelog (Unreleased, Fixed):**

- A registered extension tool without renderers is what a tool renderer
  resolver's `next()` returns, as Pi's registered definition, so
  `next() or mine` no longer fills in for it. An extension tool that
  replaces `codemode` without renderers is drawn with the generic rows,
  partial results included, in the terminal and left to the page in
  `/export` and `/share`; before, it got the built-in codemode rows, which
  hid its partial results and treated its output as a script's (see
  [Tool rendering](../extension-api.md#tool-rendering)).

**Plan:**

- DEV-45: drop "A resolver's `next()` gives `None` for an extension tool
  without renderers." and "An extension tool named `codemode` without
  renderers gets pipy's codemode rows from `next()` and without a resolver;
  Pi draws it with the generic rows."; the row then reads "pipy's own rows
  are callable renderers a resolver can wrap (since G9; also `codemode`'s
  since goal check r4, D3), with the live `edit` preview and the ticking
  `bash` time (since goal check r3, S11). `renderShell` is not ported. A
  resolver that raises, or returns something that is not renderers, is left
  out; Pi lets the exception through." Add "narrowed by X2" to its
  decision column.
- Section 4 text near l.1921 ("the extension tool of that name that has a
  renderer" becomes "the registered extension tool of that name, with or
  without renderers") and l.1948 ("`next()` gives a marker for pipy's own
  rows and `None` for an extension tool without renderers, where Pi gives
  real renderers and the tool's definition" becomes "`next()` gives a
  marker for pipy's own rows where Pi gives real renderers, and the
  registered extension tool, with or without renderers, as Pi gives the
  tool's definition"). Since goal check r5 (S1) `LiveToolRenderers`
  holds every registered tool of the generation, with or without
  renderers (Pi `getToolDefinition`), and the lookup returns it from
  `next()` and for the row; only the built-in `codemode` gets the codemode
  rows. The seven built-in tool names stay reserved, so their calls keep
  pipy's rows, which is what Pi's `withBuiltInRenderers` gives a
  replacement of them without renderers. `/export` and `/share` use the
  same lookup (`published_tool_renderer_lookup`).

**Acceptance:**

- EXT-15 row: add `test_native_extension_tool_renderer_resolver.py`:
  `test_a_codemode_replacement_without_renderers_gets_the_generic_rows`,
  `test_the_line_renderer_draws_a_rendererless_codemode_replacement_generically`;
  `test_native_export_tool_rows.py`:
  `test_a_rendererless_codemode_replacement_is_left_to_the_page`;
  `test_native_extension_live_tools.py`:
  `test_the_renderers_follow_the_latest_registration` (changed) (r5 X2).
  The limits keep "`renderShell` is not ported. DEV-45." unchanged.
- EXT-13 row: may name the same first test (a replacement's rows).

**Audit test:** the EXT-15 assertion on "`renderShell` is not ported.
DEV-45. A renderer's" stays true; add the new test names if the audit
lists EXT-15's tests.
