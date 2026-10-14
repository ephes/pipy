# MCP parity gaps, session track: notes for the merge

Track N of the MCP parity campaign works on `feat/mcp-parity-gaps-session`.
This file holds what belongs in `CHANGELOG.md`, the plan
(`docs/specs/2026-10-12-mcp-parity-plan.md`), the acceptance note
(`docs/acceptance/2026-10-13-mcp-parity.md`) and
`tests/test_mcp_parity_audit.py`; the merge into `feat/mcp-parity` folds it
in, one section per item.

## N1-a: MCP servers on a session switch (goal-check K6, E10)

### Changelog (Unreleased, Changed)

- MCP: another session in the same run (`/new`, `/resume`, `/fork`,
  `/clone`, `/import`, and the RPC and SDK `new_session`, `switch_session`,
  `fork`, `clone`) closes the MCP servers and starts them again from the
  `mcp.json` files, as Pi's MCP extension does on `session_shutdown` and
  `session_start`. The startup report and the first prompt's wait apply
  again; tools `tool_search` loaded and session-only `/mcp` changes to
  extension-registered servers do not carry over.

### Plan updates

- 4.1 "Decided in S22": replace "A new session in the same process (/new,
  /resume, /fork) keeps the manager; only /reload replaces it ... pipy fires
  no session_start for such a switch" with: a completed session transition
  (`SessionTransitionCoordinator`, every terminal, RPC and SDK path) calls
  `McpSession.replace_session()` from `_rebuild_replaced_session`, after
  history and settings. It loads the configuration again, closes the
  running manager (delivering what it still had to say), unregisters the
  MCP tools with `restart_mcp_tools(keep_declared=False)` (nothing pending,
  unlike `/reload`), and starts the new manager. The unhandled-registration
  report is reset (Pi's runner reports once per runtime).
- 4.10 "Reload and resume": add that a session switch is a restart without
  pending names.
- Section 3.1, new difference (proposal, kind "narrower"): Pi builds a new
  runtime for another session, so the whole tool selection starts from the
  configured tools; pipy restarts only the MCP tools. Non-MCP selection,
  including a `codemode` or `tool_search` that discovery turned on, is kept.
  Extensions still get no `session_shutdown`/`session_start` on a switch
  (pre-existing, not MCP-specific).
- Rows: SES-01, SES-17 and SES-20 gain the session-switch case; the S22
  limit "SES-20 for a session switch in one process ... not built" is
  closed.

### Acceptance note / audit test

- Remove the K6/E10 limit from the SES-20 row's note ("the manager is kept
  on /new, /resume, /fork"). If `tests/test_mcp_parity_audit.py` counts the
  DEV rows, the new 3.1 difference adds one.
- Tests: `tests/test_native_mcp_session_switch.py` (all five terminal
  commands with real stdio servers, the SDK `new_session()`, the
  `McpSession.replace_session` unit cases) and
  `tests/test_native_tool_capabilities_mcp.py::test_another_session_does_not_declare_loaded_tools_again`.
