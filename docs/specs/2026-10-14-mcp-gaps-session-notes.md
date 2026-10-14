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

## N2-d: commands in queued RPC input, and get_commands (goal-check E17, SLH-13)

### Changelog (Unreleased)

- Changed: RPC `steer` and `follow_up` whose text names an extension
  command (also `/mcp`) fail with Pi's `Extension command "/<name>" cannot
  be queued. Use prompt() or execute the command when not streaming.`; a
  skill command or prompt template in them, and in a `prompt` queued behind
  a run, is expanded when it is queued. A `prompt` that names an extension
  command during a run runs it at once and is answered `handled` (a failure
  is an `extension_error`).
- Changed: RPC `get_commands` lists the extension commands (with `/mcp`),
  prompt templates (with pipy's custom commands) and skills
  (`skill:<name>`); it answered an empty list before.

### Plan / backlog updates

- Row SLH-13 is closed except the `input` hooks at queue time (below). The
  plan's 3.2 audit "Open: SLH-13" and the status paragraph change to
  "closed with a limit"; DEV-42 (`get_commands` empty) is closed.
- "Decided in X1 (prompts of the automation modes)", "Not built (SLH-13)":
  replace with: `repl/session_command_port.py` `SessionCommandPort`
  (`command_name`, `run`, `expand`, `commands`), handed to the transport by
  `bind_session_command_port(observer, port)` (the RPC server's
  `bind_session_commands`) while the session is composed; it reads only
  published state (the extension generation through
  `SessionExtensionOperations.commands()`, `ctl.workspace_resources`, the MCP
  command view) and runs a command through
  `SessionCollaborators.dispatch_extension_effect`. The RPC server checks
  `steer`/`follow_up` on the command thread; a `prompt` that names a command
  while the bridge has a reservation runs on a thread of its own (so the
  command thread keeps reading and dialogs get answers), concurrently with
  the run, as Pi's does; a queued prompt is expanded before admission.
  The server tracks those command threads and, at the end of input (not on
  SIGTERM/SIGHUP), joins them without a limit after closing the UI and
  before the drain, so the `handled` response is written and the command
  runs against the live session. `SessionCommandPort.expand` leaves a text
  that does not start with `/` alone (` /review` stays text).
- Still different (new 3.1 difference, "narrower"): the `input` hooks do not
  run when a message is queued (Pi runs them in `_queueUserInput` and
  `prompt`); a queued message that later runs while idle meets them before
  its run, after expansion; one taken by the run in flight does not. The
  expansion of queued input records no resource invocation. A `prompt`
  admitted just as the run ends may be expanded twice when its expansion
  starts with `/` and names another template (race between the busy check
  and the admission). `get_commands`' `sourceInfo` has only `path`.
- docs/backlog.md (not edited on this track): the "Missing RPC commands"
  entry's "`get_commands` answers an empty list" and the "Commands in queued
  input" entry are done.

### Acceptance note / audit test

- SLH-13: tests `tests/test_native_automation_rpc_queued_commands.py`
  (refused steer/follow_up for an extension command, `/mcp` and a failing
  one; a command prompt run during a held run; its failure as
  `extension_error`; expansion of queued steer, follow-up and prompt; a
  leading space is not expanded; a command still waiting for its dialog at
  the end of input is answered before the session ends; `get_commands`).

## N2-e: commands in a product session's submit() (goal-check E18)

### Changelog (Unreleased, Changed)

- SDK: `ProductSession.submit()` handles its text as Pi's SDK
  `session.prompt()` does: an extension command (also `/mcp`, so
  `McpOptions.open_url` is reached by `/mcp login`) runs and starts no run,
  the `input` hooks may change or handle the text, and skill commands and
  prompt templates are expanded. Built-in commands of the interactive
  session and `!` stay text for the model. Before, `submit()` sent every
  text to the model unchanged.

### Plan updates

- "Decided in S38": replace "A product session runs no slash command
  (`submit()` sends its text to the model)" with: `submit()` queues its
  content as `CodingInputSource.SUBMITTED_PROMPT`
  (`CodingInputQueue.enqueue_submitted_prompt`, in the positional seeds'
  place), and the controller turns that source into a step with
  `external_prompt=True`, so the loop step runs `dispatch_prompt` as for an
  RPC prompt. Without a control bridge the handled settlement is a no-op;
  the product operation's claim is settled when `drive()` reaches idle.
  A command's notices and errors go to the `diagnostic_sink` (the session's
  local notice sink; `Extension "command:<name>" error: <message>`).
- "Decided in X1", "Not changed": remove "the product API (`submit()` still
  sends its text to the model ...)".
- DEV-51 (SDK-02 to SDK-04): remove "A product session runs no slash
  command (`submit()` sends its text to the model), so `open_url` and
  `update_config` reach the command and the manager and nothing in a product
  session uses them." Replace with: a product session has no UI, so `/mcp`
  without a subcommand opens no manager view and its status is a notice for
  the `diagnostic_sink`; `update_config` is used only by a manager change
  the embedder makes. Pi's SDK session without UI drops the notice
  (`noOpUIContext`); pipy hands it to the diagnostic sink (difference kept
  under DEV-51).
- Positional seeds (`pipy "text"` in the interactive session) are not
  changed: they still reach the model as text (separate from E18).

### Acceptance note / audit test

- SDK-02 (open_url, update_config reachable): tests
  `tests/test_product_session_commands.py` (an extension command and no run,
  a failing command as an extension error on the diagnostic sink, template
  and skill expansion, an `input` hook that handles a submitted text,
  `/mcp reconnect` and `/mcp` status); the existing
  `tests/test_product_session_api.py::test_submit_is_literal_multiline_provider_content`
  still pins that `/exit` and `!` reach the model as text.

## N3-a: the `/mcp` manager scrolls long text and copies with `app.message.copy` (goal-check E19, E21, O8)

### Changelog (Unreleased, Changed)

- MCP: a screen of the `/mcp` manager that is taller than the terminal
  shows a server's details and error, a status message or the
  authorization URL through a window that PageUp and PageDown
  (`tui.select.pageUp`/`pageDown`) scroll, with a line such as
  `… lines 1-9 of 26, pageup/pagedown to scroll`. Before, the text was cut
  short and the rest could not be read.
- Keybindings: new action `app.message.copy` (default `ctrl+x`), which can
  be rebound in `keybindings.json`. The sign-in screen of the `/mcp`
  manager copies the authorization URL with it, as Pi's does; before, the
  key was fixed to Ctrl+X.

### Plan updates

- DEV-43: remove "the copy key is fixed to Ctrl+X (pipy has no
  `app.message.copy` action)" and replace "a screen taller than the
  terminal is cut above its controls, where Pi's scrolls" with "a screen
  taller than the terminal shows the text above its controls through a
  window that PageUp/PageDown scroll, where Pi's output scrolls with the
  terminal". Add: `app.message.copy` is used only on the sign-in screen;
  Pi also copies the selected `/tree` message and the last assistant
  message with it (not ported; the editor has no copy action).
- "Decided in S34", "Narrower than Pi": remove "the copy key is fixed to
  `ctrl+x` because pipy has no `app.message.copy` action". Add: the view's
  scroll state (`_Scroll.scroll`/`page`) belongs to the screen shown; a new
  screen starts at the top, a live menu built again keeps its window, and
  drawing clamps the window to the text. A menu's text above a long list
  keeps three rows (two lines and the note), so it can always be
  scrolled. Page keys scroll only while the text does not fit and never
  when they are one of the screen's own keys (a copy key bound to PageDown
  copies). `app.message.copy` joins
  `ui/key_specs.USER_KEYBINDING_ACTIONS`; the other keys of the view
  (`tui.select.*`) stay at their defaults, as before.

### Acceptance note / audit test

- MGR-02/MGR-03 (long details and errors readable): tests
  `tests/test_native_mcp_manager_view.py::test_page_keys_scroll_a_message_that_does_not_fit`,
  `::test_a_long_error_scrolls_while_the_menu_keeps_its_keys`,
  `::test_page_keys_do_not_reach_the_input_of_the_sign_in_screen`, and the
  updated `::test_a_long_error_leaves_the_actions_on_the_screen`,
  `::test_a_long_url_leaves_the_input_and_the_keys_on_the_screen`,
  `::test_a_status_message_is_cut_short_too`.
- MGR-09 row: remove "The copy key is fixed to Ctrl+X." and "DEV-43 for the
  copy key"; add test
  `tests/test_native_mcp_manager_view.py::test_the_copy_key_is_the_users_app_message_copy_binding`.

## N3-b: the sign-in URL as a terminal hyperlink (goal-check E20)

### Changelog (Unreleased, Changed)

- MCP: the authorization URL on the `/mcp` sign-in screen and a new
  `Cmd+click to open` hint (`Ctrl+click` outside macOS) are OSC 8
  hyperlinks to the URL, as Pi's `AuthUrlComponent` draws them, so a
  terminal that supports links opens a wrapped URL on a click. The frame
  keeps such links only on the rows that ask for them.

### Plan / backlog updates

- 3.2 audit table "Closed in part" (MGR-09) and DEV-43: MGR-09 is closed;
  remove "the authorization URL is plain text, not a terminal hyperlink,
  and there is no click to open hint". Add to DEV-43 (narrower): a URL that
  is not printable ASCII up to 4096 characters stays plain text without the
  click hint (Pi links every URL); the click and copy hints are part of the
  scrolled text above the controls, so a long copy failure can be scrolled
  to instead of being cut to one line.
- "Decided in S34", "Narrower than Pi": remove "the authorization URL is
  plain text, not an OSC 8 hyperlink (the frame carries only SGR
  sequences), with no click hint". Add the design: `ansi_wrap` recognises
  OSC 8 (`hyperlink()`, `OSC8_CLOSE`; a link open at a wrap is closed and
  opened again, as pi-tui's `AnsiCodeTracker` does);
  `frame_renderer.sanitize_custom_text`/`clip_custom_text` take
  `links=True` to keep only `ESC ]8;;<[!-~]{0,4096}> ESC \` and close a link
  left open at the end of a row; `LinkedFrameLine` marks such rows so
  full-frame finishing keeps them; only `search_selector_region_lines` (the
  editor-area overlay) asks for links. `visible_len` counts no link bytes.
- docs/backlog.md: remove the item "MGR-09, one part: the authorization
  URL of the sign-in screen is plain text, not a terminal hyperlink."

### Acceptance note / audit test

- MGR-09: status closed; remove the "one part" row of the open-parts table
  (line ~75) and the note "The authorization URL is plain accent-coloured
  text, not a clickable OSC 8 hyperlink, and there is no click to open
  hint". Tests:
  `tests/test_native_mcp_manager_view.py::test_the_url_and_the_click_hint_are_links_to_the_url`,
  `::test_a_url_the_frame_cannot_link_is_plain_text`,
  `::test_what_a_failed_copy_says_can_be_scrolled_to`;
  `tests/test_native_frame_hyperlinks.py` (wrapping, sanitising, clipping,
  full-frame finishing); `tests/test_native_mcp_login_pty.py::test_escape_cancels_a_login_that_waits_for_the_browser`
  (the OSC 8 link and the click hint reach a real terminal).
- The list item "SLH-13 and the hyperlink part of MGR-09: build or accept"
  (line ~599) loses its hyperlink part.

## N3-c: codemode result rows cut like Pi's (goal-check M19)

### Changelog (Unreleased, Changed)

- Codemode: a codemode row shows the script's output as Pi's codemode
  renderer does: without the `Script completed`/`Wall time`/`Output:`
  header, collapsed to the first five wrapped lines (a long one-line JSON
  output no longer fills the screen) with `... (N more lines, ctrl+o to
  expand)`, and `Full output: <path>` when the output was spilled to a
  file. Before, codemode had the generic result preview (ten logical lines,
  no spill path).

### Plan updates

- "Decided in S35" (rendering): add that `tool_rows._other_result_rows` has
  a codemode branch (`_codemode_result_rows`, Pi
  `codemodeRenderers.renderResult` output part) after the extension and MCP
  branches; the MCP and codemode previews share `_preview_rows` (Pi
  `VisualLinePreview`, `keep: "start"`) and `_full_output_rows`.
- Section 3.1, difference (proposal, kind "other form"): the codemode call is
  still the generic `codemode code="…"` row, not Pi's highlighted script
  preview of ten visual lines, and there is no `Model calls:` cost line
  (pipy's nested record has no cost). Nested calls are drawn between the
  call and the output without Pi's spacer.

### Acceptance note / audit test

- RND-04 (codemode rows): tests
  `tests/test_native_tool_rows.py::test_codemode_result_drops_the_script_header`,
  `::test_codemode_result_counts_wrapped_lines_and_names_the_full_output`,
  `::test_codemode_result_is_empty_while_the_script_runs`,
  `::test_codemode_error_result_uses_the_error_colour`.

## N3-d: nested calls in the HTML export (goal-check M20)

### Changelog (Unreleased, Changed)

- Export: the HTML page shows the nested calls of a tool result (a codemode
  script's calls, from its `nestedCalls` record) as Pi's page does:
  `Nested calls: <n>`, and on a click one line per call with its status,
  arguments, duration and error. `docs/extension-api.md` already said the
  export shows them; now it does.

### Plan updates

- "Decided in S35" (export part of RND-03/RND-04): the page script
  (`export_distribution._HTML_TEMPLATE`) has `nestedCallsText` and
  `appendNestedCalls` (Pi `renderNestedCalls` with `formatExpandableOutput(
  text, 1)`), applied to every tool result with a `details.nestedCalls`
  record (Pi reads `result.nestedCalls`; pipy keeps the record in
  `details`). It goes into the drawn call's box (`renderedBoxes`) when the
  result is drawn there, and into the "Tool result" entry otherwise. The
  boxes are a `Map` and a call is shown only with a string `name` and
  `status` (imported files may hold anything in `details`).
- Section 3.1, difference (proposal, kind "other form"): a cancelled call
  is `⊘` (Pi's page has no icon for it and prints `?`); durations are
  rounded to whole milliseconds; codemode calls themselves are not drawn
  ahead (no `renderedTools` entry), because pipy has no codemode call
  renderer (see N3-c).

### Acceptance note / audit test

- RND-04 (export): tests
  `tests/test_native_export_nested_calls.py::test_a_result_with_nested_calls_shows_them`,
  `::test_a_drawn_call_shows_its_nested_calls_in_its_box`,
  `::test_a_result_without_nested_calls_shows_none`,
  `::test_malformed_calls_are_left_out_and_the_page_goes_on`,
  `::test_a_drawn_result_before_its_call_is_shown_on_its_own` (the page script run
  under Node with a stand-in DOM; skipped where `node` is missing).

## N4: inline terminal images (goal-check M21)

Three commits: N4-a (`terminal_image.py`, settings getters), N4-b (frame
renderer path), N4-c (tool rows, wiring, docs).

### Changelog (Unreleased, Added)

- TUI: images in tool results (MCP screenshots, codemode `image()`,
  extension tools) are drawn inline after the tool row in terminals that
  speak the Kitty graphics protocol (kitty, Ghostty, WezTerm, Warp, Herdr)
  or iTerm2 inline images, as Pi's `ToolExecutionComponent` draws a pi-tui
  `Image`. `terminal.showImages` (default on) and `terminal.imageWidthCells`
  (default 60) are honoured; `PIPY_IMAGE_PROTOCOL=kitty|iterm2|none`
  overrides the detection. Under tmux or screen, in other terminals and in
  non-terminal output the row keeps the `[Image: [<mime>] <w>x<h>]` line.

### Plan updates

- "Decided in S39" (images, part 3): replace "pipy's frames carry no inline
  images ..., so a row shows what Pi shows in a terminal without an image
  protocol" with: a row whose transcript draws images inline
  (`RowRenderInputs.inline_images`) shows `ResultImages.text`, the result's
  text without the image lines (Pi `getTextOutput` with `caps.images &&
  showImages`); otherwise it shows `tool_result_display_text` as before.
  Remove "Not built: inline images in the terminal (Pi's `Image` component
  with the Kitty and iTerm2 protocols, `terminal.showImages`,
  `imageWidthCells`)."
- New "Decided in N4" paragraph (module layout 4.1 and rendering):
  - `terminal_image.py` ports pi-tui `detectCapabilities` (image part,
    without Windows), `calculateImageCellSize`, `encodeKitty`,
    `encodeITerm2`, `deleteKittyImage` and `Image.render`
    (`image_rows`). `terminal_image_protocol(stream)` gives a protocol only
    for a TTY. `clean_base64` admits only plain base64 into an escape.
  - `SettingsManager.get_show_images()`/`get_image_width_cells()` (Pi
    `getShowImages`/`getImageWidthCells`).
  - `RenderToolResult.images`/`ToolRowResult.images` (`ResultImages`)
    carry the images from the event and from a resumed session;
    `TranscriptComponent.set_image_options(protocol, show, width_cells)`
    (wired at startup and on `/reload` by `repl.reload.apply_image_settings`)
    builds a `TerminalImage` per image for the row's
    `HistoryBlockTuple.images`; the scratch transcript of a resume copies
    the options; `ToolRowState.image_ids` keeps the Kitty ids across
    redraws.
  - `FrameBlock.images`; `block_lines(..., image_height=...)` draws them
    after the box, before its closing spacer, only for committed rows
    (`build_paint_plan`); full frames and the live region show the text
    line. An image is `rows - 1` empty rows and an `ImageFrameLine` that
    moves up, draws and (Kitty, `C=1`) moves down, so a frame row stays one
    terminal row; rows are capped to the terminal height.
    `PaintPlan.kitty_image_ids` and `ScreenState.kitty_image_ids` let
    `force_full_redraw` and the resize repaint delete the Kitty images by
    id before they clear (pi-tui main-screen `fullRender`).
- Section 3.1: DEV-19 narrower: "An image is drawn in the terminal when it
  speaks the Kitty or iTerm2 protocol and `terminal.showImages` is on;
  otherwise the row shows `[Image: [<mime>] <w>x<h>]`." Add a difference
  (proposal, kind "narrower"): no `CSI 16 t` cell-size query (9x18 pixel
  cells assumed); Kitty gets PNG only (no transcoder; other types show the
  line, below the box); data that is not plain base64 shows the line; an
  iTerm2 image gets `height=<reserved rows>` instead of `auto`; an image is
  at most as tall as the terminal; no "Show images"/"Image width" rows in
  `/settings`, changes apply on `/reload`; no `showImages` in an
  extension's render context (see M24).

### Backlog

- No new item. The `/settings` rows for the two image settings and the
  cell-size query are possible follow-ups if the owner wants them.

### Acceptance note / audit test

- "Known limits" list: replace "**Images are never drawn in the terminal**
  (DEV-19; TLS-11), and `read` still returns an error for an image file
  (DEV-04)." with "`read` still returns an error for an image file
  (DEV-04)."; TLS-11 and RES-09 notes: replace "The terminal shows a line
  that names the image and never draws it." with "The terminal draws the
  image where it can (Kitty, iTerm2), else shows a line that names it."
- Tests: `tests/test_native_terminal_image.py` (detection, base64 check,
  cell sizes, Kitty/iTerm2 bytes, rows),
  `tests/test_native_frame_images.py` (committed rows only, sanitised text
  cannot become an image, Kitty deletion before redraw/resize, rebuilt rows
  keep their images), `tests/test_native_tool_row_inline_images.py`
  (text without image lines, settings off, unsized and bad data, settings
  change redraws, live result), `tests/test_native_inline_images_pty.py`
  (a real PTY: an MCP screenshot placed with the Kitty protocol and placed
  again after Ctrl+O with its deletion first; `PIPY_IMAGE_PROTOCOL=none`
  keeps the line), `tests/test_native_settings.py::test_image_getters_are_pis`.
