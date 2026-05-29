# Pipy tool-loop TUI ergonomics: full height, native scrolling, `/copy`

Date: 2026-05-29

## Goal

Make the real product command
`pipy repl --agent pipy-native --repl-mode tool-loop` behave like a proper
terminal application:

1. Use the full available terminal height (not only the upper half) in a
   full-size window and inside a zellij pane.
2. Support reviewing prior output via scrolling in both native Ghostty and
   zellij, without breaking the live input/footer frame.
3. Provide a real, executable `/copy` command that copies the most recent
   assistant answer through a safe OS/terminal clipboard path, reports a
   local status, and never invokes the provider, tools, login/logout, or
   model switching.
4. Keep the slash menu honest (only executable product-TUI commands;
   `/model`, `/login`, `/logout` stay absent until executable).

## Root cause

`ToolLoopTerminalUi` (`src/pipy_harness/native/tui.py`) uses the **alternate
screen** (`\x1b[?1049h`) and repaints a whole frame each `paint()`, clamped to
`frame[:height]` with the history region padded to a fixed
`_DEFAULT_HISTORY_VIEW_LINES = 21`.

- **Upper-half waste:** in a 50-row window the frame is ~26 rows tall
  regardless of size; the rest is blanked by `\x1b[J`, so the footer floats
  mid-screen.
- **No scrolling:** the alternate screen has no scrollback. Neither Ghostty's
  native scrollback nor zellij's scroll mode can reveal prior content while an
  app holds the alternate screen, so review scrolling is impossible in both.

These are not tunable parameters of the alt-screen model; they are inherent to
it. The fix is architectural.

## Decision: inline scrollback model

Replace the alternate-screen whole-frame repaint with an **inline live-region**
renderer (the model used by Pi, Claude Code, and prompt_toolkit's non-fullscreen
mode):

- **Committed history** — startup chrome, submitted user messages, settled
  assistant turns, settled reasoning, tool call/result rows, notices, settings
  overlays, errors — is printed **once** into the terminal's normal buffer as it
  finalizes. It flows into the terminal's native scrollback, so scrolling up in
  Ghostty or zellij reveals it. Scroll review then works identically in any
  terminal/multiplexer that has scrollback, because we no longer suppress it.
- **Live region** — pinned at the bottom and redrawn in place on each update:
  the in-progress streaming tail (reasoning / assistant / working spinner,
  bounded to fit the screen), then the separator / input / separator / optional
  slash menu / two footer rows.

### Rendering algorithm (`paint`)

State added to `ToolLoopTerminalUi`: `_painted_block_count` (history blocks
already committed to scrollback), `_live_height` (rows the live region
currently occupies), `_live_input_row` (offset of the input line within the
live region), and a `_paint_lock` (the spinner thread and worker thread both
paint).

Each `paint()`:

1. If a live region was previously drawn, move the cursor from the input cell
   up `_live_input_row` rows, carriage-return to column 0 (now at the top of the
   live region), and `\x1b[J` to erase the old live region and anything below
   it. Committed history above is untouched.
2. Render any history blocks beyond `_painted_block_count` and print them with
   `\x1b[K\r\n` per line. They scroll up into the normal buffer (native
   scrollback). Advance `_painted_block_count`.
3. Compose the live-region lines (bounded transient tail + chrome). Print each
   with a trailing `\x1b[K`, joined by `\r\n`.
4. Reposition the visible cursor to the input cell using relative moves
   (up to the input row, `\r`, right to the cursor column). Record
   `_live_height` and `_live_input_row`.

`start()` injects startup blocks (no alt-screen) and paints. `close()` commits
any pending blocks, moves below the live region, clears downward, shows the
cursor, and emits a trailing newline (no `\x1b[?1049l`).

Raw mode (termios) is unchanged; explicit `\r\n` is kept so raw-mode output with
no LF→CRLF translation still starts each row at column 1.

### Full height

In the inline model there is no artificial history cap. Content fills the
screen from the top; once a real session exceeds the screen, the whole height is
used with the input/footer at the bottom and older content scrolled into
native scrollback. The fixed 21-line cap and the `frame[:height]` clamp no
longer gate the on-screen height.

### `render_lines()` stays as a logical model

`render_lines()` / `_frame_lines()` keep composing the full logical frame
(committed history + transient + chrome). They remain useful for unit tests of
block composition, but per the goal they are **not** the primary evidence: the
real `paint()` path is verified through PTY captures and the ANSI screen-cell
model (`terminal_screen.py`), which already replays line-feed scrolling via
`viewport_y`.

## `/copy`

A new `clipboard.py` helper copies text through a safe path with a fixed argv,
no shell, and a timeout:

- macOS: `pbcopy`.
- Linux: `wl-copy`, else `xclip -selection clipboard`, else `xsel --clipboard
  --input`.
- Fallback when no OS command is available: OSC 52 written to the terminal
  stream (works across Ghostty and, with passthrough, tmux/zellij).

It returns a structured result (method used, byte count, or an unavailable
reason). It performs no network, provider, tool, or auth action.

`/copy` is dispatched in `NativeToolReplSession.run()` alongside `/help` and
`/settings`, in the local-command branch that `continue`s without a provider
turn. It copies the most recent non-empty assistant answer (scanned from the
message history) and reports a local notice
(`pipy: copied last answer …` / `pipy: nothing to copy yet` /
`pipy: clipboard unavailable …`). No provider turn, no tool invocation.

## Slash-menu honesty

`TOOL_LOOP_TUI_SLASH_COMMAND_COMPLETIONS` becomes
`("/help", "/settings", "/copy", "/exit", "/quit")`; a `/copy` description is
added to `DEFAULT_REPL_COMMAND_DESCRIPTIONS`. `/model`, `/login`, `/logout`
remain absent. The unhandled-slash diagnostic lists `/copy` too. The
verification helper's slash-row classifier learns `/copy`.

## Preserved behavior

`/help`, `/settings`, `/exit`, `/quit`, active-Escape `Operation aborted`,
reasoning italics, the three-row submitted-prompt band, tool call/result rows,
the two-row footer/status, and metadata-first archive privacy are all unchanged;
only the paint/scroll mechanics and the new `/copy` command change.

## Tests & evidence

- Unit/logical: menu contents; `/copy` dispatch with no provider turn / no tool
  invocation; clipboard helper behavior.
- Real paint path: a `pty`-backed test runs `session.run()` over a sized PTY,
  captures raw bytes, replays through `terminal_screen`, and asserts: no
  alternate-screen entry, history scrolled into the buffer (viewport_y advances)
  while the input/footer stay at the bottom, full-height use at a tall size, and
  the cursor on the input row.
- tmux artifacts at a Ghostty-sized pane and a smaller zellij-sized pane,
  captured by the existing script harness, proving full-height use and the
  live frame.

## Docs

Update `docs/backlog.md` (Pi gap queue item 3), `docs/pi-parity.md` (the
tool-loop TUI row), and `docs/harness-spec.md` (native tool-loop TUI section)
to record the inline scrollback model, full-height behavior, `/copy`, and any
remaining Pi gaps (undo/redo, prompt history, bracketed paste, SIGWINCH/resize,
selectors, in-app `/model` selection).
