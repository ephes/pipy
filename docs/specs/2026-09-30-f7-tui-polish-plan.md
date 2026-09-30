# DF1-F7 + DF1-F2b (TUI part): TUI polish

Status: implemented (DF1-F7 + DF1-F2b (TUI) in the `docs/backlog.md` Done table; the rest is DF1-F7b).
Pi reference: `~/src/pi-mono` at `1b347794e`, `packages/coding-agent/src/`:
`modes/interactive/interactive-mode.ts` (`showStatus` 3717,
`compaction_end` 3579, `renderInitialMessages` 4084, `updateEditorBorderColor`
4385, quiet-startup header 971, listing 1723),
`modes/interactive/theme/{dark.json,theme.ts}` (`getThinkingBorderColor`,
`getBashModeBorderColor`), `modes/interactive/components/{compaction,branch}-summary-message.ts`,
`modes/interactive/components/tool-execution.ts`, `core/tools/renderers/bash.ts`,
`core/tools/bash.ts:321` (initial empty update), `modes/print-mode.ts`,
`packages/tui/src/components/editor.ts` (`renderTopBorder`/`renderBottomBorder`).

## Scope

In: the six items below. Out (new follow-ons, recorded in the backlog F7
entry): `/model <ref>` exact matching over available models and a selector of
available models only (Pi `findExactModelReferenceMatch` + the search-input
`ModelSelectorComponent`; a port of its own), the compaction summary prompt
(Pi `SUMMARIZATION_PROMPT`/`UPDATE_SUMMARIZATION_PROMPT`,
`serializeConversation`, file lists; a compaction request slice), slash
commands drawn as user bubbles, notices classified into Pi's
`showWarning`/`showError`, the non-quiet startup header content (logo,
condensed hints expanded by Ctrl+O), the `!` rows (Pi `BashExecutionComponent`)
and reasoning-text storage (touches provider adapters).

## 1. Compaction redraws the chat (F2b)

Pi, on `compaction_end` with a result: `chatContainer.clear()`, then
`renderSessionEntries(entries.slice(1))` where `entries =
buildContextEntries()` must start with the compaction, then the compaction
summary row appended **last** (its chronological position), then (with usage)
a cost notice. The header container is kept. No other status line is shown.
Failures: manual `showError(message)` / `showError("Compaction cancelled")`,
automatic `showStatus("Auto-compaction cancelled")` or an error text.

pipy: `SessionHistoryRenderer.render_after_compaction()` reads
`build_context_entries()`; when entry 0 is a `CompactionEntry` it replays
`entries[1:]` into the scratch transcript, appends the compaction summary row,
and calls `replace_conversation` (full redraw that clears scrollback, the F2/F3
path). The manual `/compact` command and the automatic preflight cut call it
in the TUI when the outcome has a result and persistence did not fail;
otherwise the existing notice is shown unchanged (plain REPL, failures,
cancellations). The successful-compaction notice (`pipy: compacted
conversation context (…)`) is not drawn in the TUI (Pi draws none) and not
written in the headless modes (Pi's print mode writes nothing for it; JSON and
RPC carry `compaction_start`/`compaction_end`); failure notices keep stderr
there. No cost notice (pipy stores no
compaction usage; USAGE1b).

Summary rows are drawn like Pi's `CompactionSummaryMessageComponent` /
`BranchSummaryMessageComponent`: a `Box(1, 1)` on `customMessageBg`, the label
bold in `customMessageLabel`, collapsed `Compacted from N tokens (` +
`ctrl+o` in `dim` + ` to expand)` in `customMessageText`, expanded the bold
header (`Compacted from N tokens` / `Branch Summary`) and the summary lines in
`customMessageText`. They are encoded as `tool_box` lines with a new `custom`
background (Pi's `Box` padding and background, like tool rows) and keep
following Ctrl+O. Deviation: the expanded summary is not Markdown-rendered.

## 2. Palette: pipy's `pi` theme uses Pi's dark theme

Values from Pi's `dark.json` resolved by Pi's own `theme.ts` (truecolor and
256-colour modes, `scratchpad/f7/pi-dark-codes.txt`):

| pipy field | Pi token | truecolor | 256 |
| --- | --- | --- | --- |
| `accent` | `accent` | `167;152;215` | `140` |
| `title` | `accent` + bold (Pi's logo has no counterpart) | `1;…167;152;215` | `1;38;5;140` |
| `section` (`[Context]`) | `mdHeading` | `205;154;34` | `172` |
| `dim` (header hints, footer, notices) | `dim` | `126;136;142` | `102` |
| `secondary_dim` (loader text, menu descriptions) | `muted` | `157;165;169` | `145` |
| new `thinking_text` (reasoning, italic) | `thinkingText` | `150;160;164` | `109` |
| `error` | `error` | `234;127;129` | `174` |
| `warning` | `warning` | `205;154;34` | `172` |
| `success` | `success` | `104;183;141` | `72` |
| `user_message_bg` | `userMessageBg` | `33;59;73` | `48;5;23` |
| `user_message_text` (+ new fallback) | `userMessageText` | `222;224;225` | `254` |
| `tool_pending_bg` / `success` / `error` fallbacks | `tool*Bg` | unchanged | `237` / `23` / `52` |
| `tool_output` fallback | `toolOutput` | unchanged | `145` |
| new `custom_message_bg` | `customMessageBg` | `58;48;85` | `48;5;59` |
| new `thinking_off` … `thinking_max`, `bash_mode` | `thinkingOff` … `thinkingMax`, `bashMode` | see file | see file |

Editor border (Pi `updateEditorBorderColor`): both input separators use the
live thinking level's colour (`off` when the model has no reasoning, as the
footer's level label decides), and `bashMode` while the input starts with `!`
(after leading whitespace). pipy's ` ! bash ` label on the bottom separator
is removed (Pi draws none). The frame gets the level from a callable the
wiring installs (live provider state), so a level change or model switch
recolours the border at the next paint. Themes without thinking colours
(`ocean`, `high-contrast`, package themes) keep their `separator` colour for
every level: the new optional fields (thinking levels, `bash_mode`,
`thinking_text`) default to `None`, and `load_theme_file` does not copy them
from the default palette when a theme file leaves them out (every other
omitted field still inherits), with a package-theme regression test. The
plain (non-TUI) REPL separator is `separator`, which the `pi` palette sets to
`thinkingOff`. Other built-in palettes are unchanged.

## 3. Ticking `Elapsed` in a running `bash` row (TOOLS3b item)

Pi's bash tool sends an empty update when it starts (`bash.ts:321`), so the
running row renders its result part at once; `renderCall` records
`startedAt` when execution starts, `renderResult` shows
`Elapsed <formatDuration(now - startedAt)>` while partial and
`Took <…>` when done, and re-renders every 1000 ms (`setInterval`) until the
final result. pipy: `ToolRowState.started_at` (monotonic) is set by the live
`TuiToolLoopRenderer.render_tool_call` (its `RenderToolCall` decision is the
execution start), never when the renderer is replaying history (the existing
`_replaying` flag that also skips the edit preview), so restored rows, answered
or not, start no ticker and show no `Elapsed`. A partial built-in `bash` row with
`started_at` renders an empty partial result, so the `Elapsed` line shows
before any output; the transcript runs a 1 s ticker (injectable scheduler,
default a daemon thread) that re-renders the pending row under the paint lock
and repaints, cancelled by `finish_tool`, `flush_pending_tool`,
`replace_conversation` and the next `start_tool`. A flushed row keeps its
last `Elapsed` (`ended_at` freezes it). `Took` keeps using the executor's
duration. Restored rows have no `started_at`, so no `Elapsed` (Pi: no
`executionStarted`).

## 4. Notices render as Pi's status line (F7)

Pi `showStatus`: a spacer, then `theme.fg("dim", message)` with one column of
padding. pipy notice blocks drop the `pipy  ` prefix for a one-space indent,
use the `dim` style, and lose the extra leading blank row (pipy blocks end
with their spacer). `TranscriptComponent.add_notice` strips the stderr prefix
`pipy: ` from each line. The hotkey statuses use Pi's texts:
`Tool output: expanded|collapsed`, `Thinking blocks: hidden|visible`,
`Thinking level: <level>`, `Current model does not support thinking`.
Deviations: other notice texts stay pipy's; warnings/errors are not
classified into Pi's `Warning:`/`Error:` lines; back-to-back statuses are not
merged into one line.

## 5. Startup (F7)

- No stale editor frame: Pi's first render runs after the startup components
  are added. The screen defers every paint until `TerminalUi.start()` seeds
  the history, so the first frame is written once; before, a footer update
  painted the editor at row 0 and the next paint's `ESC[J` from row 0 made
  tmux (`scroll-on-clear`) push that frame into scrollback.
- `quietStartup` (unless `--verbose`) hides the TUI header and resource
  listing, as Pi does (`interactive-mode.ts:971,1723`); the plain REPL
  already honoured it.

## 6. Headless modes print no chrome (F7)

Pi's `--print`, `--mode json` and `--mode rpc` write nothing to stderr but
errors. pipy wired the plain REPL for them, so the startup chrome, changelog,
the resume line, the prompt echo, the plain renderer's rows and the footer
went to stderr. When an automation observer is set (json, print and rpc all
set one) these display writes go to a discarding stream; diagnostics
(`emit_diagnostic`, extension and flag errors) and the modes' own failure lines
keep stderr.

## Tests (done-when)

- Session history: `render_after_compaction` renders kept entries then the
  summary row last; falls back when entry 0 is not a compaction; manual and
  automatic compaction in the TUI redraw and show no notice; the plain REPL
  keeps the success notice; headless modes suppress it and keep failure
  notices.
- Summary rows: collapsed/expanded styled `tool_box` lines with the `custom`
  background, Ctrl+O switches them.
- Palette: the `pi` palette values equal the table; border colour per level,
  `bashMode` for `!`, no label; other themes keep `separator`.
- Bash row: `Elapsed 0.0s` before output, ticking via a fake scheduler and
  clock, cancelled on finish/flush/replace; `Took` after; replayed rows
  (answered and unanswered) without `Elapsed` or a ticker.
- Notices: no `pipy  ` prefix, `dim` kind, `pipy: ` stripped, Pi hotkey texts.
- Startup: no paint before `start()`; quiet startup seeds no header.
- Headless: `-p`, `--mode json` and `--mode rpc` leave stderr empty for a
  successful fake run, also when the run compacts automatically; a failed
  compaction still reports on stderr.
- PTY evidence side by side with Pi: live `/compact` and automatic compaction,
  palette/background comparison, ticking `Elapsed` frames, startup scrollback,
  notices.
