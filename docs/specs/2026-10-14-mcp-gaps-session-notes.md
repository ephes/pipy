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

## N1-b: MCP notices while the session waits for input (goal-check E9)

### Changelog (Unreleased, Changed)

- MCP: the startup report and the report of a server an extension
  registered later are shown while the session waits for input, in the
  terminal editor and as an RPC `notify` request while no command runs,
  instead of with the next prompt.

### Plan updates

- 4.5 (the `deliver_notices(sink)` bullet) and "Decided in S19": the session
  also shows the manager's notices while idle. `McpSession.deliver_idle_notices()`
  hands the current manager's notices to the sink and does nothing else (no
  `mcp_servers_change` hooks, no `ensure_active`; those stay at the drain
  points and the turn start). Callers: the terminal's `Screen` runs the work
  installed with `Screen.set_idle_work` in `_poll_idle_sources`, about ten
  times a second while any key is waited for (the editor's `read_line` and
  the modal key loop); the RPC worker's `_NativeSessionControlBridge` runs
  its bound idle work (`bind_idle_work`) whenever the wake reader returns
  `_NATIVE_CONTROL_IDLE`, which `rpc._WakeChannel.readline` does after
  `_IDLE_POLL_SECONDS` (0.1 s) without a wake.
- "Decided in S34", "Still open, not owned by a slice: notices are shown
  when the session drains them, so the startup report of a session whose
  first action is `/mcp` appears after the manager was closed": closed; the
  modal key loop runs the idle work too, so the report is shown behind the
  manager view as it arises.
- Section 3.1, difference (proposal, kind "other form"): Pi shows these
  notices when they arise, also during a run; pipy shows them while the
  session waits for input, so one that arises during a run is shown after
  it. `mcp_servers_change` hooks still fire at the drain points (before a
  prompt, when a turn starts), not while idle. Non-interactive plain input,
  one-shot runs and SDK product sessions show notices at the next step as
  before.

### Acceptance note / audit test

- SES-04 and EXT-10 (report of later-registered servers): the limit
  "shown before the next prompt" becomes "shown while idle"; tests
  `tests/test_native_mcp_idle_notices.py` (PTY editor, RPC client without a
  command, bridge and wake channel units, `deliver_idle_notices`) and the
  updated `tests/test_native_automation_rpc_ui.py::test_mcp_login_takes_the_pasted_redirect_url`.

## N2-a: extension errors and notices of the automation modes (goal-check E11, E12)

### Changelog (Unreleased, Changed)

- In the existing entry "The prompt of `--print` and `--mode json` and an
  RPC `prompt` are handled as Pi's `prompt` handles them", replace "`/mcp`
  is such a command and prints the server status" with "`/mcp` is such a
  command".
- New entry: print and JSON mode have no UI, as in Pi: the notices of
  extensions and of MCP (`ctx.ui.notify`, the status and messages of `/mcp`,
  the MCP startup report) are dropped there instead of written to stderr.
  An extension command that a prompt ran and that raised is Pi's extension
  error with the exception's text: `Extension error (command:<name>):
  <message>` on stderr in print and JSON mode, an `extension_error` object
  (`extensionPath`, `event`, `error`) on stdout in RPC mode; before it was
  `pipy: extension command /<name> failed (<error type>)` on stderr in all
  three. The report of an extension-registered MCP server that nothing
  connects is such an error too (event `register_mcp_server`). See
  `docs/automation-rpc.md`, "Extension errors".

### Plan updates

- "Decided in X1 (prompts of the automation modes)", "Different from Pi":
  remove the first two bullets (notices on stderr in print/JSON mode; the
  failed command on stderr, no `extension_error`). Add to "Decided": the
  session's notice and error sinks are `repl/extension_reports.py`
  `ExtensionReports` (RPC transport, prompt mode, local);
  `CodingCommandEffects.report_extension_error(path, event, error)` is used
  by `dispatch_prompt`; `ExtensionCommandDispatch.message` and
  `ExtensionDispatchResolution.message` carry the exception's text
  (`str(err)`, the type name if that raises); `RpcUi.extension_error`;
  `McpSession(report_error=)` reports unconnected registered servers through
  it. A typed line keeps `pipy: extension command /<name> failed (<type>)`.
- "Decided in X1 (RPC UI requests)", "Notices": print and JSON mode drop
  them (the sink checks the input stream's `is_prompt`).
- Section 3.1: DEV-39 (SLH-07) is closed and can be removed; DEV-41 loses its
  first sentence (print/JSON notices on stderr). If the audit test counts DEV
  rows, removing DEV-39 changes the count; the rows can instead be marked
  closed.
- Rows SLH-07 and SLH-11 (print/JSON part) are closed.

### Acceptance note / audit test

- SLH-07 and SLH-11: drop the DEV-39 and the print/JSON part of DEV-41 from
  their notes. Tests:
  `tests/test_native_automation_prompt_dispatch.py::test_a_failing_extension_command_is_still_handled`
  (print and JSON), the four print/JSON tests there that now assert nothing
  is shown, `tests/test_native_automation_rpc_prompt_dispatch.py::test_a_failing_command_is_handled_too`,
  `tests/test_native_extension_mcp_servers.py::test_an_unconnected_server_is_an_extension_error_of_print_mode`,
  `tests/test_native_extension_dispatch.py::test_an_exception_whose_text_raises_is_still_bounded`,
  `tests/test_native_coding_session_controller.py::test_prompt_reports_a_failed_extension_command_and_starts_no_run`.

## N2-b: input hooks and templates in a prompt (goal-check E14, E15, E16)

### Changelog (Unreleased)

- Added: an `input` hook can return `InputHandled` (Pi's
  `{action: "handled"}`): later hooks do not run and no run starts. A
  handled prompt of `--print` prints nothing, `--mode json` writes only the
  header, an RPC `prompt` is answered with disposition `handled`; a typed
  line starts no run.
- Changed: the prompt of `--print`, `--mode json` and an RPC `prompt` reaches
  the `input` hooks before skill and template expansion, as in Pi, so a hook
  sees `/review x` and may turn text into a template invocation; the session
  then stores the text the hooks left (Pi stores it too). A prompt template
  named like a built-in command of the interactive session (`/model`,
  `/tree`, ...) is expanded in such a prompt.

### Plan updates

- "Decided in X1 (prompts of the automation modes)": `dispatch_prompt` now
  runs the extension command, then `CodingCommandEffects.run_input_hooks`
  (`None` = handled, which settles the claim like a command), then
  `dispatch_resource(text, prompt=True)` on the hooks' text; the resolution
  carries that text and the loop step passes an unchanged transform to
  `CodingAcceptedInputPreparer`, so the hooks run once. `prepare` returns
  `None` when the typed line's hooks handled it (loop continues).
  `dispatch_resource_command(..., prompt=True)` skips the reserved-name rule.
  `dispatch_input_hooks` returns `str | None`. New public type
  `InputHandled` (`pipy_harness.extensions`, 111 public names).
- "Different from Pi" of that block: remove "a template named like a
  built-in command is not expanded" and "a resource turn skips the `input`
  hooks" for prompts. Still different: a line typed into a session runs the
  hooks only when it is not a skill or template (pipy's typed order,
  DEV-38), and a typed line a hook handled stays in the transcript because
  the terminal drew it before the hooks ran.
- DEV-40 (SLH-09): shrink to "custom commands expand like templates; an
  unknown or unreadable skill is reported on stderr; for a typed line a
  resource turn skips the `input` hooks".
- Rows SLH-09 and SLH-10 (handled by a hook) are closed for the automation
  modes.

### Acceptance note / audit test

- SLH-09, SLH-10: tests
  `tests/test_native_automation_prompt_dispatch.py::test_input_hooks_run_before_expansion`,
  `::test_a_prompt_an_input_hook_handled_starts_no_run`,
  `::test_a_template_named_like_a_builtin_command_is_expanded`,
  `tests/test_native_automation_rpc_prompt_dispatch.py::test_a_prompt_an_input_hook_handled_is_answered_handled`,
  `tests/test_native_extension_input_hooks.py::test_a_handled_input_ends_the_chain`,
  `::test_a_typed_line_an_input_hook_handled_starts_no_run`,
  `tests/test_native_coding_session_controller.py::test_prompt_input_hooks_see_the_unexpanded_text`
  and the two tests after it,
  `tests/test_native_coding_accepted_input.py::test_a_handled_prompt_is_no_turn_and_records_nothing`.
  An idle RPC `steer`/`follow_up` that a hook handles settles its claim
  (`CodingSessionController.settle_handled_prompt`), pinned by
  `tests/test_native_automation_rpc_prompt_dispatch.py::test_an_idle_queued_message_a_hook_handled_frees_the_session`.

## N2-c: extension UI over RPC for every extension (goal-check E13)

### Changelog (Unreleased)

- Added: in RPC mode every extension has the client as its UI, as in Pi:
  `ctx.has_ui` is true, `ctx.ui.select`, `confirm`, `input` and `editor` are
  `extension_ui_request` dialogs the client answers, and `set_status`,
  `set_widget` (lines of text), `set_title`, `set_editor_text` and
  `paste_to_editor` are requests without an answer (`setStatus`,
  `setWidget`, `setTitle`, `set_editor_text`). `select`, `confirm` and
  `input` take Pi's `timeout` option (milliseconds); in RPC mode the dialog
  then ends with its default. Before, extensions had no UI in RPC mode and
  only `/mcp` sent requests.

### Plan updates

- "Decided in X1 (RPC UI requests)": `RpcUi` gained `confirm`, `editor`,
  `send` (fire-and-forget) and a `timeout` on `select`/`input`/`confirm`;
  `RpcUi.extension_driver()` returns `RpcExtensionUiDriver`, an
  `ExtensionUiDriver` over it (`dialog_timeouts = True`; terminal-only
  methods are no-ops; `apply_theme` fails with Pi's text).
  `AutomationUi.extension_driver()` is in the protocol. The wiring's
  `_ExtensionPhase.ui_driver` is the terminal's live driver or, without one,
  the transport's; it is what extension contexts get (attach `has_ui`,
  lifecycle adapter, `SessionExecutionProjections`, `SessionExtensionOperations`,
  the tool-call policy, `ReloadCommandEffects.ui_driver` for a reload's
  `session_start`). `extension_ui_driver` stays the live driver for the
  terminal's chrome. `SessionExecutionProjections` binds `generation_driver`
  only on a driver that has it. `_CollectingUi.select/confirm/input` take
  `timeout=` and pass it only to a driver with `dialog_timeouts`.
- Remove from "Different from Pi" of that block: `ctx.has_ui` false for
  extensions in RPC mode, and no `confirm`/`editor`/`setStatus`/`setWidget`/
  `setTitle`/`set_editor_text` requests or `timeout` option. Still
  different: no dialog `signal` option (an RPC `abort` does not end an
  extension's dialog), and the terminal ignores `timeout` (Pi counts down).
- `NativeRpcServer` reads stdin on its own thread from the start
  (`_read_input`): `extension_ui_response` lines are delivered at once, other
  lines queue for the command thread (`_read_loop`), and the end of the input
  closes the UI. So a `session_start` dialog (before readiness) and a dialog
  of a transition hook (on the command thread) get their answers. A dialog
  answer after its timeout is ignored (`_Asked.deadline`). Startup still has
  the 120 s readiness limit (recorded as a difference).
- DEV-41 (SLH-11): with N2-a's change, reduce to those two points plus the
  120 s startup limit.
- Row SLH-11 (RPC part for extensions) is closed.

### Acceptance note / audit test

- SLH-11: tests `tests/test_native_automation_rpc_extension_ui.py` (an
  extension command's dialogs and fire-and-forget requests end to end, a
  `session_start` hook with `has_ui` true, dialogs of `session_start` and of
  a `new_session` transition hook answered, the dialog timeout and a late
  answer, widget filtering and the terminal-only no-ops, a terminal driver
  that gets no timeout); `tests/test_native_automation_rpc.py::test_prepare_failure_publishes_startup_failure_before_command_intake`
  now pins that a command read before a failed startup never runs.
