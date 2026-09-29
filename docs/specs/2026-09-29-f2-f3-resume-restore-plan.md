# F2 + F3: render restored history, restore model and thinking on resume

Status: plan, 2026-09-29. Source: DF1-F2 and DF1-F3 in
[the DF1 acceptance note](../acceptance/2026-09-29-df1-dogfooding.md)
(scenarios 10 and 11) and the follow-ons in `docs/backlog.md`.

## Problem

- **F2.** After `-r`/`--resume`, `/resume`, `/tree` navigation and `/fork`
  the provider context is right, but the transcript shows none of the
  restored conversation. Only extension custom entries are replayed
  (`CustomEntryRenderer.replay_custom_entries_to_terminal` at startup,
  `redraw_custom_entries_for_active_branch` on `/resume`).
- **F3.** A resumed session starts at the default thinking level instead of
  its last `thinking_level_change`, and never restores its model. pipy also
  never writes `model_change` entries (`docs/session-tree.md` records this as a
  known gap), so there is nothing to restore the model from.

## Pi reference (`~/src/pi-mono` @ `4df157433`, `packages/coding-agent/src`)

### Rendering (F2)

- `InteractiveMode.renderInitialMessages()` (`modes/interactive/interactive-mode.ts:4084`)
  renders `sessionManager.buildContextEntries()` through
  `renderSessionEntries` → `renderSessionItems` (`:3962`, `:3862`).
- Callers:
  - startup, after the header and loaded resources (`:1056`);
  - every runtime replacement (`/resume`, `/new`, `/fork`, `/clone`, import):
    `rebindCurrentSession({renderBeforeBind: true})` →
    `renderCurrentSessionState()` (`:2120`), which clears the chat container
    and pending state and calls `renderInitialMessages()`; then
    `showStatus("Resumed session")` / `"Forked to new session"`;
  - `/tree` navigation: `chatContainer.clear(); renderInitialMessages();`
    then `showStatus("Navigated to selected point")` (`:5545`, `:1955`).
    Tree navigation does not touch model or thinking.
- `buildContextEntries` (`core/session-manager.ts:476`): the active path; with
  a compaction, `[latest compaction entry, entries from firstKeptEntryId up to
  the compaction, entries after it]`.
- Per entry (`renderSessionItems`, `addMessageToChat`, `addCustomEntryToChat`):
  - `message` user → `UserMessageComponent` (skill blocks get a collapsible
    `SkillInvocationMessageComponent`); `populateHistory` adds the text to the
    editor's Up-arrow history;
  - `message` assistant → `AssistantMessageComponent` (text and thinking
    blocks), then one `ToolExecutionComponent` per `toolCall`, each later
    completed by its `toolResult` (matched by `toolCallId`). An aborted/error
    assistant marks its calls with `Operation aborted` / the error. Calls with
    no result stay pending;
  - `bashExecution` (`!` shell) → `BashExecutionComponent`;
  - `custom_message` with `display` → `CustomMessageComponent` (extension
    renderer or `[customType]` label + content);
  - `custom` → extension entry renderer, skipped when none or empty;
  - `compaction` → `CompactionSummaryMessageComponent`: `[compaction]` label,
    collapsed `Compacted from {tokensBefore.toLocaleString()} tokens (ctrl+o to expand)`,
    expanded `**Compacted from N tokens**` + summary;
  - `branch_summary` → `BranchSummaryMessageComponent`: `[branch]` label,
    collapsed `Branch summary (ctrl+o to expand)`, expanded
    `**Branch Summary**` + summary;
  - `model_change`, `thinking_level_change`, `label`, `session_info` render
    nothing.
- Every collapsible component takes `setExpanded(this.toolOutputExpanded)`,
  so the Ctrl+O state at render time applies, and Ctrl+O later toggles them.
- The full re-render clears the screen and the scrollback
  (`tui-main-screen.ts:283`, `\x1b[2J\x1b[H\x1b[3J`). The first render at
  startup writes without clearing.
- `renderInitialMessages` then shows `Session compacted N times` when the file
  has compactions.

### Model and thinking (F3)

- **Recording** (`core/sdk.ts:427-437`): a session with no messages gets
  `model_change(model)` and `thinking_level_change(level)` appended when its
  runtime is created; an existing session without a `thinking_level_change`
  on its branch gets one. `AgentSession.setModel` (`core/agent-session.ts:2388`)
  and model cycling append `model_change` on every switch;
  `setThinkingLevel` appends `thinking_level_change` only when the level
  changes (pipy already does this, UX1).
- **Restoring** (`core/sdk.ts:194-263`), with `hasExistingSession =
  buildSessionContext().messages.length > 0`:
  - model: when no CLI model was given and the session has one
    (`getBranchSelection`: the latest `model_change` on the branch, or an
    assistant message's provider/model), use it if
    `getModel(provider, id)` exists and `hasConfiguredAuth(provider)`;
    otherwise `modelFallbackMessage = "Could not restore model P/M"`, fall
    through to `findInitialModel` (settings default, then first available),
    and append `". Using X/Y"`;
  - thinking: CLI `--thinking` wins; else, for an existing session, the
    branch's last `thinking_level_change` if the branch has one, else the
    settings default ?? `medium`; then clamp to the model.
  - the fallback message is shown as a startup warning
    (`interactive-mode.ts:1169`); it is not shown after a runtime switch.
- **Runtime replacement** (`/resume`, `/fork`, `/clone`, `/new`, import,
  RPC `switch_session`/`fork`/`new_session`) rebuilds the whole runtime through
  `createRuntime` (`main.ts:723`), which reapplies the CLI `--model`/`--thinking`
  and runs the same restore. The footer is rebuilt with the runtime.

## pipy design

### F2: one active-branch renderer

New module `src/pipy_harness/native/ui/components/session_history.py`,
owner `SessionHistoryRenderer`:

- Reads the live tree through a callable (the tree is rebound by transitions),
  snapshots `(entries, leaf)` under the tree's mutation lock, and projects
  **render entries** with a new `session_tree.build_context_entries(entries,
  leaf_id)` (Pi's name, beside `build_coding_context`): the latest compaction
  entry (if any) followed by the same retained entries
  `build_coding_context` uses (`_retained_context_entries`, which already
  handles legacy and anchored cuts).
- Replays them into a **scratch** `TranscriptComponent` (own `PaintLock`,
  no-op repaint) through a scratch `TuiToolLoopRenderer` with the live one's
  tool-renderer map and render inputs, and a `CustomEntryRenderer` whose
  terminal target points at the scratch transcript. So restored rows are
  produced by exactly the verbs that render live turns:
  - user message → `render_user_message(text)`; a `!` shell record (the
    `I ran a shell command in the workspace (not a tool call):` text that
    `local_shell.py` stores) → the same `$ command` tool row and
    status/output result row the live shell writes;
  - assistant → `render_buffered_assistant_text(text)` +
    `complete_assistant_message`, then per `tool_call`:
    `render_tool_call(call)` and, when a later `toolResult` on the branch
    (before the next assistant/user message) has the same
    `provider_correlation_id`, `render_tool_result(output_text=…, is_error=…)`
    (read-hiding, `ls` shaping, extension `render_call`/`render_result` and
    the Ctrl+O preview all come from that renderer). A call with no result
    renders only its call row;
  - `custom` / `custom_message` (display) → the existing custom renderers;
  - `compaction` / `branch_summary` → new collapsible summary rows (below);
  - other entries render nothing.
- Publishes with a new `TranscriptComponent.replace_conversation(blocks)`.
  The startup boundary is recorded by `seed_history`, which `TerminalUi.start()`
  calls exactly once with the startup chrome (`startup_history_blocks`: header
  and `[Context]`/`[Skills]` blocks) — Pi's header and loaded-resources
  containers, which a session replacement does not clear. Everything appended
  after it (the startup changelog notice, diagnostics, turns) is Pi's chat
  container and is replaced. When nothing follows the boundary it appends and
  repaints (startup: Pi's first render does not clear); otherwise it clears the
  live buffers (assistant, reasoning, tool output, working, deferred reasoning)
  and calls the screen's new scrollback-clearing full redraw
  (`force_full_redraw(clear_scrollback=True)` → `\x1b[2J\x1b[H\x1b[3J`), like
  Pi's clearing full render. A transcript that was never seeded (boundary 0)
  replaces all rows.
- Summary rows: `HistoryBlockTuple("custom", …, SummaryRenderState)` with
  Pi's text; `TranscriptComponent._rerender_custom_messages_locked` re-renders
  them on Ctrl+O like the retained custom rows, so they start collapsed (or
  expanded when Ctrl+O is on) and toggle.
- Tool results re-render on Ctrl+O too (Pi `setExpanded` on every
  `ToolExecutionComponent`). Today `TuiToolLoopRenderer.render_tool_result`
  picks the five-line preview when it commits and Ctrl+O only refreshes custom
  rows. The preview choice moves into the transcript: the renderer commits the
  full visible result lines through a new `add_collapsible_tool_result`, whose
  block keeps a `ToolResultRenderState` (full lines, error flag, duration) and
  is re-rendered by the same Ctrl+O pass. Live and restored results share it.
  The `!` shell row keeps its full-output behavior.

Call sites (terminal only; headless sessions have nothing to render):

- startup: `_start_chrome` renders the active branch after
  `terminal_ui.start()`, replacing `replay_custom_entries_to_terminal()`
  (which is then unused and removed);
- `SessionCommandEffects`: after a completed `/resume` (picker, `<id>`, the
  non-terminal fallback), `/new`, `/fork`, `/clone`, and after a `/tree`
  selection or accepted branch-summary selection — before the existing
  diagnostic, so the notice lands after the redraw like Pi's `showStatus`.
  It replaces the `redraw_custom_entries_for_active_branch` field there
  (`/reload` keeps its custom-entry redraw).
- `/import` (transfer effects) after a completed import.

### F3: record and restore model and thinking

- **CLI overrides.** `NativeReplProviderState` gains `cli_selection` and
  `cli_thinking_level` (both optional): the model the CLI pinned with
  `--native-provider`/`--native-model` and the `--thinking` level. They are
  Pi's `options.model`/`options.thinkingLevel`, reapplied at every runtime
  replacement.
- **Pure decision.** New `src/pipy_harness/native/session_settings.py`:
  `resolve_session_settings(tree_context, branch, *, cli_selection,
  cli_thinking, fallback_selection, default_thinking, usable)` returns the
  target selection, the target level (unclamped), whether the session needs a
  `model_change`/`thinking_level_change` recorded, and the fallback message.
  It encodes the Pi rules above; `usable(selection)` is the caller's
  "catalog row exists, provider available, model available" check.
- **Startup** (`cli._tool_repl_adapter_for`, the pipy analog of
  `createAgentSession`): when an opened session has messages, resolve before
  the provider state is finalized, so the first provider, footer and requests
  use the restored model and level. The fallback message is printed as
  `pipy: Could not restore model P/M. Using X/Y` on stderr before the TUI
  starts (the existing startup-warning pattern for `--thinking`).
- **Recording.** Pi keeps a new session's `model_change` and
  `thinking_level_change` in memory and writes them with its first message
  (lazy session file). pipy writes its file eagerly, so it appends them just
  before a branch's first message (`session_settings.record_session_start`,
  called from the product-session message append); a session nobody used stays
  empty, and bare `/fork`/`/clone`/`/export` of it keep refusing. An existing
  branch without a thinking entry gets one with the restored level
  (`ProviderMutationEffects.sync_session_settings`, at wiring startup and
  after every completed session transition).
- **Runtime restore** (same method, after `/resume`, `/fork`, `/clone`,
  import, and the RPC transition port): for a session with messages, resolve
  the target and, if it differs from the live model/level, prepare and commit
  one model mutation (off-lock provider construction, the existing commit
  checks) with the target level and **no default persistence** and **no tree
  append**, then refresh the footer. If the restored model is unusable
  (unavailable, or not tool-capable), the live model is kept and nothing is
  shown (Pi shows the fallback only at startup). Failures are fail-soft
  diagnostics; the transition itself has already completed.
  Tree navigation does not call it.
- **Model switches record `model_change`.** `apply_model_selection`
  (`/model`, the selector, scoped cycling), `extension_set_model` and RPC
  `set_model`/`cycle_model` append `model_change` after a committed switch.
  RPC commits inside the queue gate, so its pending-append queue becomes a
  queue of tree appends (model then thinking, commit order).

## Deviations kept (documented)

- pipy assistant messages carry no provider/model, so the model is restored
  only from `model_change` entries; sessions written before this change
  restore no model.
- pipy stores no reasoning text, tool-result details or durations, so restored
  history has no thinking blocks, extension result details or `Took Ns`.
- No `Operation aborted` marks (pipy persists no aborted assistant message,
  DF1-F6), no skill-invocation component (pipy does not store Pi's `<skill>`
  block), no Up-arrow history population (pipy keeps its own prompt-history
  store), no `Session compacted N times` status.
- Runtime fallback keeps the live model rather than re-running
  settings-default/first-available selection; `/new` keeps the live model and
  level (Pi re-resolves CLI/settings defaults) and records them.
- Live `/compact` still shows its notice instead of the `[compaction]` row
  (owned by the concurrent compaction slice).
- Extension-rendered tool rows (`render_call`/`render_result` through
  `add_tool_call_custom`/`add_tool_result_custom`) keep the expansion state of
  the moment they were rendered, live and restored alike; Pi re-renders them
  on Ctrl+O. Retaining the renderer inputs (args, per-call state, result,
  details) for a re-render is recorded as a follow-on in `docs/backlog.md`;
  this slice changes only plain tool results.

## Tests (done-when)

- `build_context_entries`: no compaction, legacy compaction, anchored
  compaction; the latest compaction first.
- Session history render: user, assistant + tool call/result pairs (read
  hidden, error), unmatched call, `!` shell record, custom entry and displayed
  custom message, compaction and branch summary rows collapsed/expanded and
  toggled by Ctrl+O; live and restored tool results expanded and collapsed by
  Ctrl+O; startup blocks kept and a startup notice after them replaced; append
  vs scrollback-clearing redraw.
- `/resume`, `/fork`, `/clone`, `/new`, `/tree` selection and branch-summary
  selection render the new branch before their notice.
- `resolve_session_settings` matrix: CLI model/thinking win; session model
  restored when usable, fallback message when not; thinking from the entry,
  settings default or `medium` when absent; clamp; empty session records.
- Startup: `-r` of a session with `model_change` + `thinking_level_change`
  starts on that model and level (provider built with it); missing thinking
  entry appended; new session gets both entries.
- Runtime: `/resume` restores and rebuilds the provider without appending
  model/thinking entries or saving a default; an unusable model keeps the
  live one; `/tree` does not change model/thinking.
- `/model`, extension `setModel`, RPC `set_model` append `model_change`
  (RPC order: model then clamped thinking).
- Real tmux PTY run with the fake provider and an isolated `HOME`,
  `PIPY_CONFIG_HOME`, `PIPY_SESSION_DIR` (metadata archive) and
  `PIPY_NATIVE_SESSIONS_ROOT` (native session files; `--session-dir` is the
  per-invocation alternative): frames after `-r`, `/resume`, `/tree` and
  `/fork` show the history; footer shows the restored model/level.
- `just check` green; docs (`docs/session-tree.md`, `docs/tui-workflow.md`),
  `CHANGELOG.md` `[Unreleased]` and `docs/backlog.md` (F2/F3 → Done) updated.
