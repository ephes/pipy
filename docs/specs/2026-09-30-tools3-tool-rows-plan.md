# TOOLS3 + DF1-F2b (part): Pi tool rows, tool-result details, re-renderable rows

Status: implemented (TOOLS3 + DF1-F2b (part) in the `docs/backlog.md` Done table).
Pi reference: `~/src/pi-mono` at `1b347794e`, `packages/coding-agent/src/`:
`core/tools/renderers/{index,read,grep,find,ls,write,edit,bash}.ts`,
`core/tools/render-utils.ts`, `core/tools/{read,grep,find,ls,edit,bash}.ts`
(details), `core/tools/edit-diff.ts` (`computeEditsDiff`),
`modes/interactive/components/{tool-execution,diff,keybinding-hints}.ts`,
`modes/interactive/interactive-mode.ts` (`renderSessionItems`,
`setToolsExpanded`), `modes/interactive/theme/{theme.ts,dark.json}`,
`packages/tui/src/utils.ts` (`wrapTextWithAnsi`), `node_modules/diff`
(`diffWords`).

## What Pi does

Every tool call is one `ToolExecutionComponent`: a `Spacer(1)` and then a
`Box(paddingX 1, paddingY 1)` whose background is `toolPendingBg` while the
call runs and `toolSuccessBg` / `toolErrorBg` once the result arrives. Inside
the box the tool's `renderCall` component is followed by its `renderResult`
component. `edit` uses `renderShell: "self"` and is exempt from that rule:
its call component is its own box whose background (`getEditHeaderBg`) is
decided by the retained preview first (success for a diff, error for a preview
error), then by `settledError` only when there is no preview (error), else
pending. So a successful preview followed by a failed execution keeps the
success background. Its result lines follow the box without a background. The component keeps its inputs (args,
result content, details, `isError`, renderer state) and re-renders on
`setExpanded`, which Ctrl+O calls on every tool component, live and restored.

Built-in renderers (`withBuiltInRenderers`; an extension definition's own
`renderCall`/`renderResult` wins per method):

| Tool | Call | Result, collapsed | Result, expanded |
| --- | --- | --- | --- |
| read | `read <path>` (accent), `:start-end` (warning). Collapsed and classified: `[skill] <dir>`, `read docs <rel>` (under the package root: `README.md`, `docs/`, `examples/`), `read resource <rel>` (`AGENTS.md`, `AGENTS.override.md`, `AGENTS.MD`, `CLAUDE.md`, `CLAUDE.MD`) plus ` (ctrl+o to expand)` | nothing unless an error | every line (highlighted when the extension has a language), `[Truncated: …]` / `[First line exceeds …]` from `details.truncation` |
| grep | `grep /pat/ in path (glob) limit N` | first 15 lines, `... (N more lines, ctrl+o to expand)`; then `[Truncated: N matches limit, 50KB limit, some lines truncated]` from details | all lines; the same warning |
| find | `find pat in path (limit N)` | first 20 lines, the same hint; `[Truncated: N results limit, 50KB limit]` | all; the same warning |
| ls | `ls path (limit N)` | first 20 lines, the same hint; `[Truncated: N entries limit, 50KB limit]` | all; the same warning |
| write | `write <path>`, blank, content preview: 10 lines then `... (N more lines, T total, ctrl+o to expand)`; expanded all lines | success: nothing; error: blank + error text | same |
| edit | `edit <path>`; with a preview: blank + `renderDiff` (context muted, removed red, added green, one-line change gets `diffWords` inverse spans) or the preview error in red | error text unless equal to the preview error; a result diff that differs from the preview | same |
| bash | `$ <command>` (bold) + ` (timeout Ns)` muted | last 5 visual lines, `... (N earlier lines, ctrl+o to expand)` first; `[Full output: p. Truncated: showing X of Y lines]` warning (the output's own `Full output` notice is cut); `Took Ns` (`formatDuration`) only when the live run recorded a start | all lines |
| no renderer | `formatToolCallWithArgs` (title + `key=value` pairs cut to 100 chars; expanded `key: value` lines) | first 10 lines, `... (N more lines, ctrl+o to expand)` | all |

Tool results carry `details` (Pi `ToolResultMessage.details`), stored in the
session file and not sent to providers: read `{truncation}` when truncated;
grep `{truncation?, matchLimitReached?, linesTruncated?}`; find
`{truncation?, resultLimitReached?}`; ls `{truncation?, entryLimitReached?}`
(each omitted when empty); edit `{diff, patch, firstChangedLine}`; bash
`{truncation, fullOutputPath}` when truncated; write none. `truncation` is the
whole `TruncationResult` in camelCase. Extension tools store their
`ToolResult.details`.

Durations are not stored: a restored `bash` row has no `Took` line, and no
other built-in row ever shows one.

`edit` preview and result, in order (`renderers/edit.ts`):

1. Live, once the arguments are complete, `renderCall` computes a preview
   (`computeEditsDiff` against the file as it is then) and draws it in the
   call box (success background for a diff, error background for an error).
   Restored rows never compute one (`setArgsComplete` is not called).
2. `renderResult` first updates the call box from the result: a successful
   result's `details.diff` (string) replaces the preview (with
   `details.firstChangedLine`), and `settledError` follows `isError`; the box
   is rebuilt when either changed. This happens live and on restore, so a
   restored edit shows the stored diff inside its success box and a result
   diff that differs from the live preview replaces it.
3. Only then are the result lines formatted against the (updated) preview:
   an error text is shown after the box unless it equals the preview error; a
   success `details.diff` is shown after the box only when it differs from
   the preview, which after step 2 happens only when there is no call
   component. Nothing else is shown for a success.

## pipy changes (this slice)

1. **Details channel.** `ToolExecutionResult.details: Mapping[str, object] |
   None` (JSON object, validated by a `json.dumps` round trip), copied onto
   `AgentToolResultMessage.details` (`compare=False`, so history equality is
   unchanged) by the executor, carried by `ToolCallCompleted` →
   `RenderToolResult`, written to the session tree `tool` message as
   `details` when not `None` and read back (strictly: an object). Provider
   requests never read it. The extension tool port puts the JSON-safe copy of
   `ToolResult.details` on the result (a value that does not serialize becomes
   `None`, live and stored alike) and the private render-details sink
   (`ToolRenderDetailsSink` and its wiring) is removed.
2. **Built-in details.** read/grep/find/ls/bash produce the Pi fields above
   from their existing truncation state (`TruncationResult` → camelCase dict).
   edit returns `{diff, firstChangedLine}`; `patch` (jsdiff
   `createTwoFilesPatch`, no renderer consumer) stays a follow-on.
3. **Renderers** (`native/tool_rows.py`, pure): Pi's per-tool `renderCall`/
   `renderResult` and the fallbacks above, producing styled logical lines
   through a Pi-key theme (`toolTitle`, `toolOutput`, `accent`, `warning`,
   `error`, `muted`, `dim`, `toolDiff*`, `customMessageLabel/Text`, `bold`,
   `inverse`; fg-only resets so the box background survives). `renderDiff`
   with a ported `diffWords` (jsdiff 8 tokenizer + whitespace dedupe),
   checked differentially against Pi's module via node. `formatDuration`
   rounds like JS `toFixed`. `computeEditsDiff` runs on the live call (before
   the tool executes) from the existing `edit_diff` port; the result then
   updates the retained preview exactly as in steps 2-3 above (tests: a
   restored edit, a result diff differing from the live preview, a failure
   whose text equals the preview error, a failure that differs, and a
   successful preview followed by a failed execution, which keeps the success
   background).
4. **One row per tool execution.** The TUI keeps a `ToolExecutionRenderState`
   per call (name, args, extension tool or built-in, renderer state, preview,
   result content, details, `is_error`, duration). While the call runs its box
   is drawn in the live region with the pending background (a running `bash`
   shows its streamed output through the bash renderer's partial result);
   when the result arrives the whole row is committed once with the success or
   error background. Ctrl+O re-renders every retained tool row, built-in and
   extension, live and restored (extension `render_call`/`render_result` are
   called again with the new `expanded`, the retained args/state/content/
   details). The `$ ` prompt, the `tool`/`tool_read` header rows, the generic
   `[error] tool reported a failure` line, `Took` on non-bash rows and the
   `edit`/`write` side-output rows are removed. Frame rendering: a `tool_box`
   block of tagged lines (background: pending/success/error/none; wrap or
   clip), wrapped with an ANSI-aware port of `wrapTextWithAnsi` inside Pi's
   one-column padding, backgrounds from three new palette fields plus
   `tool_output` (Pi dark values: `#34383a`, `#254131`, `#5b282a`, `#9da5a9`).
5. **Restore.** `SessionHistoryRenderer` passes stored `details` to the
   result render; no live preview and no duration, but the stored
   `details.diff` fills the edit box (step 2 above).

## Deviations kept (documented)

- No syntax highlighting (Pi uses highlight.js through `cli-highlight`; the
  stdlib has none): lines Pi would highlight are drawn in the plain text
  colour, other lines in `toolOutput`.
- No file hyperlinks (OSC 8), no image blocks (pipy results are text).
- The running `bash` box shows no ticking `Elapsed Ns`.
- Colours other than the four new fields come from pipy's palette
  (`accent`, `warning`, `error`, `dim`), which already differs from Pi's
  current dark theme.
- The captured (non-TTY) renderer keeps its layout. Its only change is the
  details source: an extension `render_result` now gets `ctx.details` from
  the result (`RenderToolResult.details`) instead of the removed sink
  (tested with a captured extension result that reads `ctx.details`); edit
  and write no longer print side output, so it prints the edit diff from
  `details.diff` and the write content from the call arguments where the
  side output used to appear.

## Follow-ons (stay in the backlog)

- DF1-F2b: live `/compact` and auto-compaction redraw; reasoning text stored
  and shown on restore (touches the assistant message model and provider
  replay).
- JSON/RPC `toolResult.details` and `tool_execution_end.result.details`.
- edit `details.patch`.
- The RPC `bash` command still runs through the allowlisted sandbox.

## Done when

- Unit tests pin each renderer's collapsed/expanded lines against the Pi
  texts above (including collapsed truncation warnings), `renderDiff`/`diffWords` against a node differential,
  details written/read by the session tree and ignored by provider requests,
  Ctrl+O re-rendering of built-in and extension rows (live and restored).
- tmux evidence: Pi and pipy for read/grep/find/ls/write/edit/bash rows,
  collapsed and expanded, live and restored, same row texts.
- `just check`, every `scripts/parity_checks/*.py` and
  `scripts/parity_score.sh` pass; docs, backlog and CHANGELOG updated.
