# Pi-Style Interactive TUI Workflow Depth

Status: **shipped.** Researched from the local Pi reference on 2026-06-02 and
implemented through pipy-owned Python boundaries. All milestones below have
landed (the `@` picker + Tab path completion, `!`/`!!` shell shortcuts, model +
thinking-level hotkeys, `Ctrl+O`/`Ctrl+T` folding, queued steering/follow-up,
true cancellation, clipboard/drag image paste, the `/scoped-models` + `/hotkeys`
overlays and new `/settings` rows, and the mouse-selection invariant) and are
proven by `scripts/parity_checks/tui_workflow_conformance.py --json` plus the
real-PTY product-path tests in `tests/test_native_tool_loop_tui_pty.py`. This
document remains the behavioral source of truth for the track.

This document defines the pipy target for reaching real feature parity with
Pi's interactive TUI/editor workflow depth. It is based on the local reference
checkout at `/Users/jochen/src/pi-mono`, and it specifies the remaining
interaction-comfort gaps through pipy-owned Python boundaries. This is not a
TypeScript port of Pi's TUI and not a port of the `@earendil-works/pi-tui`
toolkit. Pipy's core product TUI stays a stdlib raw-mode renderer; the parity
goal is user-facing behavior equivalence, not source compatibility.

Pipy's product TUI already covers the daily-driver basics: inline (non-alternate
screen) scrollback, the slash-command menu, `/settings`, the `/model` selector,
in-memory and optional persistent prompt history, ANSI bracketed paste,
Ctrl-Z/Ctrl-Y undo/redo, poll-based resize handling, `/copy`, typed `@path` and
`@image:<path>` references in a submitted prompt, and streaming reasoning shown
as italic "thinking" text. Those landed surfaces are out of scope here except as
the boundaries this track extends. This document specifies the **remaining**
gaps only.

## Sources

Pi reference (read for exact keybindings and behavior):

- `packages/coding-agent/src/core/keybindings.ts` — the authoritative keybinding
  registry and default keys for every interactive hotkey named below.
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts` — the editor
  wiring, the `!`/`!!` bash path (`handleBashCommand`, `onChange` bash-mode
  border), clipboard image paste (`handleClipboardImagePaste`,
  `onPasteImage`), follow-up/dequeue queueing (`handleFollowUp`,
  `handleDequeue`, `restoreQueuedMessagesToEditor`,
  `updatePendingMessagesDisplay`), scoped/all-model cycling (`cycleModel`),
  thinking-level cycle (`cycleThinkingLevel`), tool-output expansion
  (`toggleToolOutputExpansion` / `setToolsExpanded`), thinking-block fold
  (`toggleThinkingBlockVisibility`), the autocomplete provider wiring
  (`createBaseAutocompleteProvider`, `setupAutocompleteProvider`), and the
  startup help/hint text (the `expandedInstructions` / `compactInstructions`
  blocks).
- `packages/tui/src/autocomplete.ts` — `CombinedAutocompleteProvider`: the `@`
  file picker (uses `fd` to walk, then `scoreEntry` exact/prefix/substring
  scoring ~:692-742, not fuzzy subsequence), `~/` expansion, quoted paths for
  spaces, the `@`-prefix extractor, and forced-Tab path completion
  (`getFileSuggestions` ~:556-685: `readdirSync` + case-insensitive `startsWith`,
  directories-first then alphabetical). `fuzzyFilter` is used only for
  slash-command completion (~:322), not for `@` files or path completion.
- `packages/coding-agent/src/modes/interactive/components/scoped-models-selector.ts`
  — the `/scoped-models` selector that defines the cycling scope.
- `packages/coding-agent/src/core/agent-session.ts` — `steer`/`followUp`/queue
  state (`_steeringMessages`, `_followUpMessages`, `queue_update` event),
  `cycleModel` scoped/available logic, `cycleThinkingLevel`
  (`THINKING_LEVELS = ["off","minimal","low","medium","high"]`), and `abort()`.
- `packages/agent/src/agent.ts` — the abort path: `abort()` calls
  `activeRun.abortController.abort()`, and the run's `AbortSignal` flows into
  listeners and the provider call.
- `packages/ai/src/providers/openai-codex-responses.ts` — confirms the signal
  reaches the live HTTP request: `fetch(url, { ..., signal: options?.signal })`.
  Pi cancellation actually cancels the in-flight request, not just late chunks.

Pipy current state (the boundaries this track extends):

- `docs/harness-spec.md` (ToolLoopTerminalUi / product-TUI section, ~513-729).
- `docs/backlog.md` (Pi Gap Queue item 3; Current Largest Gaps item 3).
- `src/pipy_harness/native/tui.py` — `ToolLoopTerminalUi`: `read_line`,
  `wait_for_active_turn_interrupt`, `run_model_selector`, `run_settings_dialog`,
  `run_tree_selector`, the live-region/frame renderer, and the startup footer
  (which already advertises `! bash` and `ctrl+o more`).
- `src/pipy_harness/native/repl_input.py` — the stdlib raw-mode key reader and
  key tokens (`esc`, `tab`, `shift-enter`, `ctrl-c`, `ctrl-d`, `ctrl-u`,
  `ctrl-z`, `ctrl-y`, `paste`).
- `src/pipy_harness/native/tool_loop_session.py` — `_complete_provider_turn` /
  `wait_for_active_turn_interrupt` wiring. True provider-request cancellation
  has **shipped** here: the turn builds a `CancelToken`, and Escape/Ctrl-C close
  the in-flight `urllib` response and reap the daemon worker (see *True
  Provider-Request Cancellation — SHIPPED* below).
- `src/pipy_harness/native/cancellation.py` — `CancelToken` /
  `ProviderCancelledError`, the pipy-owned cancellation primitive threaded into
  `ProviderPort.complete(...)` and the HTTP-client boundaries.
- `src/pipy_harness/native/provider.py` — `ProviderPort.complete(...)`, which
  now carries the optional `cancel_token`.
- `src/pipy_harness/native/file_references.py`,
  `src/pipy_harness/native/image_attachment.py`,
  `src/pipy_harness/native/clipboard.py` — the existing `@path` / `@image:`
  resolution and OS-clipboard helpers reused here.

## Target Outcome / Goal

`pipy repl --agent pipy-native --repl-mode tool-loop` reaches Pi-equivalent
interactive comfort: the product editor offers an `@` file picker and broader
path completion, image data can be pasted/dropped (not only typed as
`@image:`), the editor runs ad-hoc shell with `!`/`!!`, and a rich set of
in-frame hotkeys (model cycling, thinking-level cycle, output/thinking folding,
tool-output expansion, queued steering/follow-up) operate without a provider
turn except where one is explicitly requested. Pressing the interrupt key during
an active turn actually cancels the in-flight provider HTTP request, and the
editor supports terminal-native mouse text selection.

Every behavior is delivered through pipy-owned Python boundaries, stays
stdlib-only with no new runtime dependency, and preserves inline
(non-alternate-screen) rendering. The metadata-first archive privacy posture is
not a constraint to re-prove here because the TUI is a presentation boundary and
does not write the archive directly; the existing native session-tree and
archive ports continue to own persistence and redaction.

The track may land in reviewed milestones, but the objective goal is the full
set of behaviors below, verified by the conformance gate in the Verification
Plan.

## Keybinding Reference

These are Pi's default keys (from `core/keybindings.ts`). Pipy adopts the same
defaults where its stdlib raw-mode reader can decode the chord; bindings pipy
cannot yet decode are documented as known input-decoding limits rather than
silently dropped.

| Action | Pi action id | Pi default key |
| --- | --- | --- |
| Cancel / abort | `app.interrupt` | `escape` |
| Clear editor | `app.clear` | `ctrl+c` |
| Exit when empty | `app.exit` | `ctrl+d` |
| Cycle thinking level | `app.thinking.cycle` | `shift+tab` |
| Cycle model forward | `app.model.cycleForward` | `ctrl+p` |
| Cycle model backward | `app.model.cycleBackward` | `shift+ctrl+p` |
| Open model selector | `app.model.select` | `ctrl+l` |
| Toggle tool output | `app.tools.expand` | `ctrl+o` |
| Toggle thinking blocks | `app.thinking.toggle` | `ctrl+t` |
| External editor | `app.editor.external` | `ctrl+g` |
| Queue follow-up message | `app.message.followUp` | `alt+enter` |
| Restore queued messages | `app.message.dequeue` | `alt+up` |
| Paste image from clipboard | `app.clipboard.pasteImage` | `ctrl+v` (`alt+v` on Windows) |
| Path completion / accept autocomplete | `tui.input.tab` | `tab` |

`@` file-picker triggering and mouse selection are not single keybindings: `@`
is a content trigger handled by the autocomplete provider, and mouse selection
is a terminal-level mode, both specified in their own subsections.

## `@` File Picker

**Pi behavior** (`packages/tui/src/autocomplete.ts`,
`CombinedAutocompleteProvider`): while typing in the editor, a token beginning
with `@` opens an inline file-attachment completion. The provider extracts the
`@`-prefixed token before the cursor (`extractPathPrefix` / the `@` branch),
runs `fd` to walk the workspace tree (respecting `.gitignore`, fast), then ranks
candidates with `scoreEntry` (~:692-742): an exact filename match scores 100, a
filename that `startsWith` the query 80, a substring in the filename 50, a
substring in the full path 30, with a `+10` bonus for directories; entries that
score `0` are dropped and the rest sort by descending score (top ~20). This is
exact/prefix/substring scoring, **not** fuzzy subsequence matching — Pi would not
match `@srctuiconfig` to `src/tui/config.ts`; the query must appear as an
ordered substring of the filename or path. (`fuzzyFilter` is reserved for
slash-command completion, ~:322.) `~/` expands to the home directory, paths
containing spaces are emitted quoted (`@"my dir/file"`), directories keep a
trailing `/`, and accepting a suggestion replaces the `@`-token with the chosen
`@path`. The popup is keyboard-navigable (up/down move, Enter/Tab accept, Escape
closes) and windows to a configured max-visible row count. Moving the caret
(`←`/`→`/`Home`/`End`) dismisses the popup — its replacement span is anchored to
the caret offset where it opened, so it reopens on the next edit rather than
splicing a candidate at a stale offset.

**Pipy target** (extends `repl_input.py` editor + `tui.py` live region, reusing
`file_references.py`): add a pipy-owned autocomplete provider that detects an
`@`-prefixed token at the cursor and offers a scored list of workspace-relative
paths. Because pipy is stdlib-only and must not require `fd`, the candidate walk
uses `os.scandir`/`os.walk` with a bounded breadth (depth and entry caps) and a
default-deny of `.git` and other ignored roots, matching the existing
`ReadTool`/`file_references` path policy. Ranking mirrors Pi's `scoreEntry`:
case-insensitive exact filename (highest), filename prefix, filename substring,
then full-path substring, with a directory bonus, dropping zero-score entries and
sorting by descending score. It is explicitly exact/prefix/substring scoring, not
a fuzzy subsequence matcher; no new dependency. The popup renders in the pinned
live region below the input like the slash menu (six visible rows + scroll
indicator), is keyboard-navigable (Up/Down, Tab/Enter accept, Esc close), expands
`~/`, quotes paths with spaces, keeps a trailing `/` on directories, and replaces
only the `@`-token on accept. Accepting leaves a literal `@path` in the buffer so
the existing `file_references` resolver loads the bounded excerpt on submit — the
picker is a typing aid, not a second attachment path. The slash menu keeps
priority when the token starts with `/`; the `@` picker and `/` menu never open at
once.

## Path Completion In The Editor

**Pi behavior**: beyond `@`, the editor offers natural path completion. The
`extractPathPrefix` natural trigger returns a candidate prefix when the text
before the cursor looks like a path (contains `/`, starts with `.`, or starts
with `~/`), and `Tab` forces completion even from an empty/space-trailing
context. `getFileSuggestions` (~:556-685) lists the entries of the prefix's
directory with `readdirSync`, keeps those whose name **case-insensitively
`startsWith`** the typed file prefix, and sorts them directories-first then
alphabetically (no fuzzy ranking), with the same `~/` expansion, space-quoting,
and trailing-slash rules as the `@` picker. This makes `./src/<Tab>` or
`~/pr<Tab>` complete inline without an `@`.

**Pipy target** (same editor/provider boundary as the `@` picker): Tab completes
against the directory listing for the token before the cursor using a single
bounded stdlib `os.scandir` of the prefix's directory, case-insensitive
`startswith` prefix matching, and directories-first-then-alphabetical ordering
(not fuzzy ranking), with the same `~/` expansion, space-quoting, and
trailing-slash behavior. Matching Pi's forced extraction (`extractPathPrefix`
with the Tab `forceExtract` flag "always returns something"), Tab does not
require a path-like token: a bare workspace prefix such as `scr<Tab>` or
`read<Tab>` completes to a matching entry (`scripts/`, `README.md`) just as
`./src/<Tab>` or `~/pr<Tab>` does — Pi force-completes any token whose name
prefix-matches a workspace entry. Tab is a no-op only when there is nothing to
extend: an empty/space-trailing token, or a token (path-like or prose) that
matches no workspace entry — in which case Tab inserts nothing rather than a tab
character. A bare Tab on a path-like prefix completes the longest unambiguous
segment and then re-opens the popup for the next segment. Completion is
read-only filesystem inspection scoped by the existing path policy: for a
workspace-relative directory it applies the same `.git`/ignored-generated deny
and symlink-containment check as the `@` picker (so it never offers an ignored
entry such as `node_modules/` or a symlink escaping the workspace), while
explicit absolute/`~/` navigation the user pointed Tab at is listed as-is (Pi
parity). It never reads file contents and never invokes the provider.

## Clipboard / Drag Image Paste

**Pi behavior**: `Ctrl+V` (`app.clipboard.pasteImage`; `alt+v` on Windows) calls
`handleClipboardImagePaste`: it reads the OS clipboard image (`readClipboardImage`),
writes the bytes to a temp file (`pi-clipboard-<uuid>.<ext>` in the OS temp
dir), and inserts that file path at the cursor so the normal attachment path
picks it up. Drag-and-drop of files onto the terminal is supported too — the
startup hint reads `drop files to attach`; the dropped path text arrives as
editor input and is treated as an attachment reference.

**Pipy target** (extends `repl_input.py` key handling + reuses
`image_attachment.py` and `clipboard.py`): bind the interrupt-safe paste-image
key (default `ctrl+v`, decoded by the raw-mode reader) to a pipy clipboard-image
read. Add a stdlib-only OS image read helper alongside the existing
`copy_to_clipboard` text helper — on macOS via `pngpaste` when present or an
AppleScript/`osascript` clipboard-to-PNG fallback, on Linux via
`wl-paste`/`xclip` reading `image/png` — that returns raw bytes and a MIME type,
or `None` when no image is present (errors are silently ignored, matching Pi).
The bytes are written to a private temp file with owner-only permissions, and
the editor inserts an `@image:<path>` reference at the cursor (the alias the
existing `image_attachment.py` resolver already accepts), so submit attaches it
through `ProviderImageAttachment` with no new attachment plumbing. Terminal
drag-drop is handled by treating bracketed-paste / pasted text that resolves to
an existing image file path as an `@image:` reference and other existing paths
as `@path` references; pure-text drops fall through to ordinary paste. When no
clipboard tool is available the action emits a local status notice and does
nothing else. No image bytes ever reach the metadata archive (the existing
attachment redaction already covers this).

## `!` / `!!` Shell Shortcuts (Run Bash From The Editor)

**Pi behavior** (`interactive-mode.ts`): a submitted line starting with `!` runs
a shell command from the editor without a provider turn. `!cmd` runs `cmd` and
records the command + output into the conversation context; `!!cmd` runs `cmd`
but excludes it from context (`excludeFromContext`). Output streams live into a
`BashExecutionComponent`. While the editor text starts with `!` the editor
border switches to a bash-mode color (`onChange` sets `isBashMode`). If a bash
command is already running, a new `!` submit is refused with a warning ("Press
Esc to cancel it first.") and the text is kept. If the agent is streaming, the
bash component is parked in the pending area; it is moved to chat via
`flushPendingBashComponents()` (interactive-mode.ts ~:2636, 3809-3815) at the
start of the **next normal prompt submit**, not automatically on `agent_end`.
`Esc` aborts a running bash command (`session.abortBash`). The startup hints
read `! to run bash` and `!! to run bash (no context)`.

**Pipy target** (extends `tool_loop_session.py` submit dispatch + `tui.py`):
recognize a submitted line whose first non-space character is `!` as a local
shell shortcut handled before any provider turn. `!cmd` runs `cmd` through the
existing real-bash execution boundary (the same bounded streaming shell used by
the model-visible `bash` tool: combined stdout/stderr, output bounds, optional
timeout, live streaming into a shaded result block) and records the
command/output into the in-memory conversation context and the native session
tree so the next provider turn and resume see it. `!!cmd` runs identically but
is excluded from provider context (no conversation/native-tree message; it stays
a live-only diagnostic). The input cell shows a bash-mode affordance (border /
prompt-label color change) while the buffer starts with `!`, mirroring Pi.
A `!` submit while a turn is streaming parks the bash run's output block in the
pending region and moves it into committed history at the start of the next
normal prompt submit (matching Pi's `flushPendingBashComponents` timing, not an
automatic flush on turn settle); a `!` submit while a bash command is already
running is refused with a local warning and the text is restored. Escape cancels a
running `!` command (terminating the child process group) without aborting the
session. This reuses pipy's existing bash tool execution and bounding; it adds no
new sandbox surface and runs no provider turn.

## Scoped-Model Cycling (Ctrl+P)

**Pi behavior** (`cycleModel` in `agent-session.ts`): `Ctrl+P`
(`app.model.cycleForward`) and `Shift+Ctrl+P` (`app.model.cycleBackward`) cycle
the active model without opening the selector. If a scoped model set is
configured (`/scoped-models`, `--models`), cycling rotates only through the
scoped, auth-available subset and applies each entry's saved thinking level
(explicit scoped level overrides; undefined inherits the session level, clamped
to model capability). With no scope, cycling rotates through all auth-available
models. Cycling persists the new default provider/model, emits a `model_select`
event, updates the footer/border, and shows a status (`Switched to <model>` or
`Only one model in scope` / `Only one model available`).

**Pipy target** (extends `tui.py` key handling + the
`NativeReplProviderState.model_options()` / `select_model` boundary already used
by `/model`): bind `ctrl+p` / `shift+ctrl+p` (decoded by the raw-mode reader) to
forward/backward cycling through the available model list reported by
`model_options()`, skipping `[unavailable: ...]` entries and refusing providers
that do not advertise tool-call support (the same constraint `/model` enforces).
Add a pipy-owned scoped-model set, backed by the non-secret local settings store,
so cycling can be narrowed to a curated subset; when no scope is configured,
cycle the full available list. On each cycle pipy reuses `select_model` (rebind
provider, clear in-memory context, rebind usage meter, refresh footer/status
label, persist the non-secret default) and appends a `model_change` native-tree
entry, exactly as `/model` does. A single-entry scope or single-available list
shows the corresponding status and does nothing else. Cycling runs no provider
turn. A full `/scoped-models` selector overlay is part of the richer-overlays
subsection; cycling works against the configured scope without it.

## Thinking-Level Hotkeys

**Pi behavior**: `Shift+Tab` (`app.thinking.cycle`) cycles the reasoning level
through `THINKING_LEVELS = ["off", "minimal", "low", "medium", "high"]`, clamped
to what the active model supports. It updates the footer and the editor border
color (`getThinkingBorderColor`), shows a status (`Thinking level: <level>` or
`Current model does not support thinking`), and appends a `thinking_level_change`
session entry. This is distinct from `Ctrl+T` (`app.thinking.toggle`), which
folds/unfolds rendered thinking blocks (see folding below).

**Pipy target** (extends `tui.py` key handling + the provider/runtime thinking
state): bind `shift+tab` to cycle the thinking level through the same ordered
levels, clamped to the active model's advertised reasoning support. Pipy already
streams reasoning as italic thinking text and already persists
`thinking_level_change` native-tree entries (per the session-tree spec), so this
hotkey sets the runtime level, refreshes the footer/border affordance, shows a
local status, and appends the `thinking_level_change` entry. Models without
reasoning support report "does not support thinking" and the level stays `off`.
The hotkey runs no provider turn; the new level applies to the next turn.

## Output/Thinking Folding (Ctrl+T) And Tool-Output Expansion (Ctrl+O)

**Pi behavior**: `Ctrl+O` (`app.tools.expand`) toggles tool-output expansion —
`setToolsExpanded` walks the chat container and the header, expanding/collapsing
every `Expandable` (tool-call/result rows and the startup header) so full tool
output is shown or summarized. `Ctrl+T` (`app.thinking.toggle`) toggles thinking
blocks via `toggleThinkingBlockVisibility`: it flips `hideThinkingBlock`,
persists the setting, rebuilds the chat from session messages with the new
visibility, re-applies it to any in-flight streaming component, and shows a
status (`Thinking blocks: hidden|visible`). In the tree selector, `Ctrl+O` and
`Ctrl+T` are remapped to filter cycling/no-tools (see session-tree.md); the
global meaning above applies in the main editor view.

**Pipy target** (extends `tui.py` rendering + history model): because pipy
renders inline and commits finalized blocks into native scrollback, "folding" is
a forward-looking render-mode toggle plus a re-render of the live history the UI
still owns, not a mutation of bytes already in the host terminal's scrollback.
Add two pipy-owned view flags on `ToolLoopTerminalUi`:

- `tools_expanded` (toggled by `ctrl+o`): when collapsed (default), tool-result
  blocks render the existing bounded preview; when expanded, they render the
  full retained tool output up to the existing output bound. The flag governs
  how newly committed and live tool blocks render, and triggers a coherent
  repaint of the live region; already-scrolled-off blocks keep whatever form
  they were committed with (documented inline limitation versus Pi's full
  retro-rebuild, which pipy intentionally does not do to preserve native
  scrollback).
- `thinking_hidden` (toggled by `ctrl+t`): hides or shows reasoning/thinking
  blocks for subsequent and live rendering, persisted in the non-secret local
  settings store, with a `Thinking blocks: hidden|visible` status.

Both toggles run no provider turn and mutate only renderer view state plus the
non-secret settings file. When the tree selector is open, `ctrl+o`/`ctrl+t`
follow the session-tree spec's filter semantics instead.

## Queued Steering / Follow-Up During Active Turns

**Pi behavior** (`agent-session.ts` queue state + `interactive-mode.ts`): while a
turn is streaming, a normal Enter submit calls `prompt(text, { streamingBehavior:
"steer" })` — a **steering** message that interrupts/redirects the running agent
at the next safe point. `Alt+Enter` (`app.message.followUp`) calls `prompt(text,
{ streamingBehavior: "followUp" })` — a **follow-up** queued to run after the
current agent run settles. Queued messages render in a pending area
(`updatePendingMessagesDisplay`) as `Steering: ...` / `Follow-up: ...` lines with
a hint, and the session emits `queue_update` events. `Alt+Up`
(`app.message.dequeue`) restores all queued messages back into the editor
(`restoreQueuedMessagesToEditor`), joined by blank lines, for editing. The
abort/escape path also restores queued messages to the editor before aborting.
Extension commands execute immediately rather than queueing. When idle,
`Alt+Enter` behaves like Enter.

**Pipy target** (extends `tool_loop_session.py` turn loop + `tui.py`): add a
pipy-owned per-session message queue with two lanes, steering and follow-up.
Submitting a non-command line during an active turn enqueues it as a steering
message; the active-turn input watcher (today `wait_for_active_turn_interrupt`,
which must keep reading keys during the turn) accepts editor input mid-turn.
Bind `alt+enter` to enqueue a follow-up (and to act as plain submit when idle),
and `alt+up` to drain both lanes back into the editor joined by blank lines.
Render a pending-messages region in the pinned live area showing `Steering: ...`
and `Follow-up: ...` lines plus a "restore" hint, repainted on every enqueue and
on turn settle. After the current turn settles, drain steering messages first
(redirecting the next turn) then follow-up messages, preserving order. The
existing Escape-abort path drains the queue back into the editor before
cancelling, matching Pi. Local commands recognized by the dispatcher run
immediately rather than queueing: pressing Enter on a `/…` slash command or a
`!…` bash shortcut mid-turn (like Pi's editor `onSubmit`) interrupts the turn
and dispatches the command locally on the next loop iteration — it is never
steered or sent to the model. Only ordinary prose becomes a steering message,
so the queue lanes hold prompt text exclusively. Follow-ups (`Alt+Enter`) are
queued as-is without the slash check, matching Pi. Queued bodies are ordinary
prompts and flow through the same native-session persistence on delivery;
nothing extra reaches the archive. Because a queued message is provider-visible
prompt text, on delivery it bypasses local-command dispatch: a drained line that
happens to begin with `/` or `!` (e.g. an `Alt+Enter` follow-up) is sent to the
model verbatim (still resolving any `@file`/`@image` references) rather than
being re-interpreted as a slash command or `!`-shell shortcut and dropped.
Because steering requires interrupting a live turn at a safe point,
this milestone composes with true cancellation below: a steering message that
must interrupt the model uses the same cancel token to stop the in-flight
request, then re-issues the turn with the steering text appended.

## Richer Overlays / Selectors

**Pi behavior**: beyond `/model`, Pi exposes several keyboard-navigable
overlays: `/scoped-models` (`scoped-models-selector.ts` — multi-select with
provider toggle, reorder, enable-all/clear-all, save), `/settings`
(`settings-selector.ts` — sectioned, many actionable rows), a session selector
(`/resume`), the tree selector (`/tree`), a thinking selector, a theme selector,
and a hotkeys reference (`/hotkeys`). Overlays draw in place of the editor, run
no provider turn, and share keybinding-hint footers.

**Pipy target** (extends `tui.py` selector framework): pipy already has
`run_model_selector`, `run_settings_dialog`, and `run_tree_selector` as in-frame
overlays drawn in the pinned live region. This subsection closes the remaining
overlay set as pipy-owned overlays reusing that framework and the inline
contract (no alternate screen, coherent repaint on resize, Esc/Ctrl-C/Ctrl-D
cancel, no provider turn):

- A `/scoped-models` overlay: multi-select over `model_options()` to define the
  cycling scope (toggle membership, save to the non-secret settings store),
  with Pi-style provider toggle and enable-all/clear-all actions where they map
  cleanly. Saving updates the scope used by Ctrl+P cycling above.
- A `/hotkeys` overlay (and the startup help/footer text) that lists pipy's
  effective keybindings, including the new hotkeys and content triggers from
  this track, so the editor advertises only what the dispatcher can actually do
  (honest-menu posture, matching the existing slash menu).
- The session-tree spec already owns the `/tree`, `/resume`, `/fork`, `/clone`,
  and theme/settings overlays; this track only adds the scoped-models and
  hotkeys overlays and the new actionable rows (scoped models, thinking level,
  folding toggles) inside the existing `/settings` dialog.

Captured-stream (non-TTY) fallbacks print a concise read-only diagnostic for
each overlay and never fall through as a provider prompt.

## Mouse Selection

**Pi behavior**: in the interactive editor the user can select and copy text
with the mouse using the terminal's native selection. Pi does not capture mouse
events for its own use in the editor, so the terminal/multiplexer performs the
selection over the rendered scrollback and live region.

**Pipy target** (constraint on `tui.py` raw-mode setup): pipy must keep
terminal-native mouse text selection working. The renderer must **not** enable
xterm mouse-tracking modes (no `ESC[?1000h` / `?1002h` / `?1003h` / `?1006h`),
so the host terminal and multiplexer keep ownership of click-drag selection over
both committed native scrollback and the live region. Because pipy already
renders inline and commits finalized blocks into native scrollback, ordinary
terminal selection already spans prior output; this track makes that an explicit
invariant and adds a real-PTY assertion that no mouse-tracking enable sequence is
emitted. Pipy does not implement its own in-app selection region or copy gesture
beyond the existing `/copy`; mouse selection is delegated to the terminal by
design.

## True Provider-Request Cancellation — SHIPPED

**Pi behavior**: `Escape` (`app.interrupt`) during an active turn calls
`agent.abort()`, which calls `activeRun.abortController.abort()`. That
`AbortSignal` flows through the agent run into the provider call, where it is
passed to `fetch(url, { ..., signal })` (e.g.
`openai-codex-responses.ts:246`). The HTTP request is actually cancelled — the
connection is torn down, no further tokens are billed/streamed, and the run
settles with `stopReason: "aborted"`. Listeners also receive the signal so they
can stop their own work.

**Pipy behavior (shipped)**: Escape **and** Ctrl-C during an active turn now
cancel the live provider request at its boundary — they no longer merely hide
late output. The implementation is stdlib-only and lives entirely in pipy-owned
boundaries:

1. `pipy_harness/native/cancellation.py` defines `CancelToken` (a thread-safe
   `threading.Event` plus a registry of in-flight closeables) and
   `ProviderCancelledError`. `ProviderPort.complete(...)` and the
   `JsonHTTPClient.post_json` / `SseHTTPClient.post_sse` boundaries take an
   optional keyword-only `cancel_token`.
2. The shared `open_url_cancellable` / `urlopen_read_cancellable` helpers (in
   `_provider_helpers.py`) open the request through a custom `urllib` opener
   whose connection **registers itself on the token at `connect()` time** — so
   the registered closeable exists before any response object does. This is
   load-bearing: a non-streaming JSON API does not send response headers until
   the model finishes generating, so the worker blocks *inside*
   `urlopen()`/`getresponse()` the whole time; registering only the post-`urlopen`
   response would miss that window entirely. When the token is cancelled the
   closer calls `socket.shutdown(SHUT_RDWR)` (not just `close()`, which a live
   `makefile()` reader would defer), which force-unblocks the worker's blocked
   `recv` during the header wait *or* a later body/stream read, raising
   `ProviderCancelledError` — pipy's stdlib equivalent of aborting `fetch`. The
   custom opener subclasses the default HTTP/HTTPS handlers, so proxy, redirect,
   and HTTP-error handling stay intact. Every catalog JSON adapter, the Codex
   SSE adapter, and the `fake` provider observe the token; adapters also
   `raise_if_cancelled()` before issuing the request, so a pre-flight cancel is
   honored too.
3. `tool_loop_session._complete_provider_turn` builds one `CancelToken` per
   turn, uses its event for the existing late-chunk suppression, and passes it
   into `provider.complete(...)`. On an Escape return **or** an active-turn
   Ctrl-C (`KeyboardInterrupt` from `wait_for_active_turn_interrupt`) it calls
   `cancel_token.cancel()` (shutting the connection down), best-effort joins the
   daemon worker (bounded by `_CANCEL_JOIN_TIMEOUT_SECONDS`), renders the red
   `Operation aborted` state, and returns `None`. Cancellation is cooperative:
   every shipped adapter observes the token, so the worker unwinds and the join
   reclaims it promptly. Even a provider that *ignored* the token could not
   corrupt the session — the turn returns `None` (so no assistant/tool/context
   mutation is appended) and late stream/reasoning chunks are suppressed, so an
   abandoned daemon worker can only finish its own request and have its output
   discarded.
4. Active-turn Ctrl-C is now equivalent to Escape: it aborts the turn and
   returns to a usable prompt instead of tearing the session down. (Ctrl-C at
   the idle input prompt still clears/exits as before.)

**Session-tree / context on abort**: the user's prompt for the aborted turn is
recorded normally (the user did type it), but the loop breaks before any
`AssistantMessage`/tool observation is appended, so an aborted turn never
records a misleading successful assistant or tool result, and the next
provider request carries the user prompt with no fabricated assistant reply in
between. No secret/auth payload enters the metadata archive — the abort path
adds no metadata and `ProviderCancelledError` carries no provider payload.

**Coverage**: focused unit tests around the boundary
(`tests/test_native_cancellation.py`,
`tests/test_native_provider_cancellation.py` — including a real local socket
that proves `cancel()` interrupts a blocked `read()`, and a fake-HTTP proof
that an adapter forwards the token), `_complete_provider_turn` Escape/Ctrl-C
tests and a session-honesty test in `tests/test_native_tool_loop_*`, and a
real-PTY product test
(`test_pty_active_turn_interrupt_cancels_and_returns_to_prompt`) that drives the
actual Escape and Ctrl-C key sequences during a live turn and asserts the
aborted state plus a usable follow-up prompt.

This was the load-bearing correctness fix of the track: Escape/Ctrl-C during a
live turn closes the socket so no further provider tokens stream or are billed,
rather than only hiding chunks that keep arriving.

## Invariants

- Pipy-owned Python boundaries only. This is not a TypeScript port and not a
  port of `@earendil-works/pi-tui`. Behaviors are delivered through pipy's
  `ToolLoopTerminalUi`, `repl_input`, `tool_loop_session`, `ProviderPort`, and
  the existing `file_references` / `image_attachment` / `clipboard` /
  provider-state / native-session-tree boundaries.
- Stdlib-only. No new runtime dependency. The completion scorer, file walk, image
  clipboard read, and HTTP cancellation are implemented with the standard
  library (`os`, `threading`, `urllib`, `http.client`, `subprocess` for
  OS-clipboard tools). The optional `prompt-toolkit` adapter may be referenced as
  an alternate input runtime, but the core product TUI stays the stdlib raw-mode
  renderer; none of these features may depend on `prompt-toolkit` being present.
- Inline rendering preserved. No alternate screen, no scrollback suppression.
  New overlays and popups draw only in the pinned live region; folding/expansion
  changes how the UI-owned live history and future blocks render and never
  rewrites bytes already committed to native scrollback.
- Mouse-tracking modes stay off. The renderer must not enable xterm mouse
  reporting, so terminal-native selection keeps working over scrollback and the
  live region.
- No surprise provider turns. Every hotkey and overlay in this track runs no
  provider turn except: a steering Enter or `alt+enter` follow-up that is an
  ordinary prompt, and a `!`/`!!` command (which runs a shell, not the provider).
  Local slash commands recognized by the dispatcher execute immediately and do
  not queue.
- True cancellation means HTTP teardown. Escape during a live turn must close the
  in-flight provider request/socket, not merely suppress late chunks.
- Honest affordances. Footer hints, the slash menu, the `@`/path popup, and the
  `/hotkeys` overlay advertise only what the dispatcher can actually do at the
  current state, matching pipy's existing honest-menu posture.
- Path/image safety. The `@` picker, path completion, and drag/clipboard image
  paste respect the existing workspace path policy, `.git`/ignored default-deny,
  symlink/path-escape checks, and output bounds. Image bytes are written to
  owner-only temp files and never enter the metadata archive.
- Archive privacy is owned elsewhere. The TUI does not write the archive
  directly; persistence and redaction stay behind the native session-tree and
  archive ports. Metadata-first privacy is therefore not re-proven as a
  constraint of this track, but no new code path may route prompts, command
  output, image bytes, or provider payloads into the default archive.
- Captured-stream parity. Every interactive surface degrades to a deterministic
  line-oriented diagnostic on non-TTY streams and never falls through as a
  provider prompt.

## Implementation Milestones

The track may land in reviewed milestones. Group sensibly:

1. **Editor completion core**: pipy-owned exact/prefix/substring scorer
   (mirroring Pi's `scoreEntry`) and bounded stdlib path walk (path policy
   aware), the `@` file picker popup in the live region, and Tab path completion
   in the editor (prefix-match, dirs-first ordering). Reuses `file_references`
   for resolution on submit. (`@` picker + path completion.)
2. **Shell shortcuts**: `!`/`!!` editor submit dispatch reusing the real bash
   execution boundary, bash-mode border affordance, context vs no-context
   recording, queue-while-streaming, refuse-while-running, and Escape-cancel of a
   running command.
3. **Model/thinking hotkeys**: `ctrl+p`/`shift+ctrl+p` model cycling over
   `model_options()` with a pipy-owned scoped set, and `shift+tab` thinking-level
   cycling, both reusing `select_model` / the runtime thinking state and
   appending the matching native-tree entries.
4. **Folding/expansion**: `ctrl+o` tool-output expansion and `ctrl+t`
   thinking-block fold as renderer view flags with coherent live repaint and a
   persisted thinking-visibility setting.
5. **Queued steering / follow-up**: the two-lane per-session queue, mid-turn
   editor input acceptance, `alt+enter` follow-up, `alt+up` dequeue, the pending
   region rendering, drain-on-settle ordering, and queue-restore-on-abort.
6. **True provider-request cancellation**: extend `ProviderPort.complete` with a
   cancel token, wire HTTP-response teardown into every adapter's streaming
   helper, raise/handle `ProviderCancelled`, and join the provider worker on
   abort. (Compose with steering interruption from milestone 5.)
7. **Image paste / drop**: stdlib OS image-clipboard read helper, `ctrl+v` image
   paste inserting an `@image:` reference via owner-only temp files, and
   terminal-drag path/image reference handling.
8. **Richer overlays**: `/scoped-models` overlay (defining the cycling scope),
   `/hotkeys` overlay, and the new actionable rows (scoped models, thinking
   level, folding toggles) inside the existing `/settings` dialog.
9. **Mouse selection invariant**: assert mouse-tracking modes stay off and
   document terminal-native selection as the supported behavior.

Milestone 6 (true cancellation) is the load-bearing correctness fix and should
not be deferred behind the comfort features; milestone 5 depends on it for
steering interruption.

## Verification Plan

### Real-PTY test contracts

Mirror pipy's existing `tests/test_native_tool_loop_tui_pty.py` real-PTY
approach (drive the real product command in a PTY, replay output through
`pipy_harness.native.terminal_screen`, assert on visible rows/columns/cursor,
viewport/scroll state, and SGR cell attributes). Add product-path PTY tests at
the existing small and larger sizes (e.g. 80x24 and 100x40):

- **`@` picker**: type `@`, then a substring query, assert the popup lists
  score-ranked workspace paths (exact/prefix/substring, dirs bonus) in the live
  region, that a non-substring query like `@srctuiconfig` does **not** match
  `src/tui/config.ts`, Up/Down moves selection, Tab/Enter accepts and replaces
  the `@`-token with the chosen `@path`, Esc closes, and the slash menu does not
  co-open. Assert a space-containing path is quoted and a directory keeps a
  trailing `/`.
- **Path completion**: type `./` then Tab and `~/` then Tab; assert inline
  directory-entry completion with the same quoting/expansion rules, and that Tab
  in mid-prose is a no-op.
- **`!`/`!!` shell**: submit `!echo hi`; assert a shaded bash block streams
  `hi`, no provider turn occurs, and context records the command; submit
  `!!echo secret`; assert output shows but no context/native-tree message is
  recorded; assert the bash-mode border appears while the buffer starts with `!`;
  assert a second `!` while one runs is refused; assert Escape cancels a long
  `!sleep` command without ending the session.
- **Model cycling**: press `ctrl+p`/`shift+ctrl+p`; assert the footer model
  label changes through the available (or scoped) list, unavailable/non-tool
  providers are skipped, a single-entry scope shows the status, and no provider
  turn runs.
- **Thinking cycle**: press `shift+tab`; assert the level cycles
  off→minimal→low→medium→high (clamped), the footer/border updates, and the
  status shows the new level or "does not support thinking".
- **Folding/expansion**: with a settled tool block present, press `ctrl+o` and
  assert the live region toggles between bounded preview and full bounded output;
  press `ctrl+t` and assert reasoning blocks hide/show with the status notice and
  that the setting persists across a fresh session.
- **Queued steering / follow-up**: during a streaming fake-provider turn, submit
  a line (steering) and `alt+enter` a line (follow-up); assert both render in the
  pending region as `Steering:`/`Follow-up:` lines; press `alt+up` and assert
  both are restored into the editor joined by blank lines; let the turn settle
  and assert steering drains before follow-up; press Escape mid-turn and assert
  the queue is restored to the editor before the turn aborts.
- **Image paste**: with a fake clipboard-image helper, press `ctrl+v`; assert an
  `@image:<temp>` reference is inserted, the temp file is owner-only, and submit
  attaches it through `ProviderImageAttachment`; assert no clipboard tool yields
  a status notice and no insertion.
- **Mouse selection**: assert the PTY output contains no xterm mouse-tracking
  enable sequence (`?1000h`/`?1002h`/`?1003h`/`?1006h`) during startup, idle,
  overlay-open, and active-turn states.
- **True cancellation**: with a deterministic slow/streaming fake provider that
  records whether its HTTP-equivalent read was torn down, submit a long prompt,
  sample `Working...`, press Escape, and assert the post-Escape frame shows red
  `Operation aborted` with no resumed stream **and** that the provider's
  cancel-token was observed and its response was closed before completion (no
  further chunks were produced after abort). This extends the existing
  active-turn Escape audit to assert real teardown, not just suppressed chunks.

### Deterministic conformance gate

Add a top-level deterministic conformance script driven by the fake provider in
a temporary workspace, analogous to the session-tree gate:

```sh
uv run python scripts/parity_checks/tui_workflow_conformance.py --json
```

It must fail unless, through the product PTY path with the fake provider:

1. an `@` query opens the picker and accepting replaces the token with a valid
   `@path` that the file-reference resolver then loads on submit;
2. Tab path completion completes a directory prefix and is a no-op in prose;
3. `!cmd` runs without a provider turn and records context; `!!cmd` runs without
   recording context;
4. `ctrl+p`/`shift+ctrl+p` change the active model through the scoped/available
   list with no provider turn;
5. `shift+tab` cycles the thinking level through the ordered levels and appends a
   `thinking_level_change` native-tree entry;
6. `ctrl+o` and `ctrl+t` toggle tool-output expansion and thinking visibility as
   renderer view flags with the persisted thinking setting;
7. steering and follow-up messages queue during a turn, render in the pending
   region, dequeue to the editor, and drain in steering-then-follow-up order;
8. a clipboard-image paste inserts an owner-only `@image:` reference that attaches
   on submit;
9. mouse-tracking enable sequences are never emitted;
10. Escape during a live turn sets the cancel token, the provider observes it,
    the response/connection is closed, the run settles `aborted`, and no further
    chunks arrive; the context and native-session tree remain consistent;
11. every interactive surface degrades to a deterministic non-TTY diagnostic and
    never falls through as a provider prompt;
12. no prompt body, command output, image bytes, or provider payload reaches the
    default metadata archive through any new code path.

Before treating implementation as complete, run:

```sh
uv run python scripts/parity_checks/tui_workflow_conformance.py --json
uv run pytest tests/test_native_tool_loop_tui_pty.py
uv run pytest tests/test_native_repl_pty_chrome.py
just check
```

### Optional tmux Pi comparison smoke

Optionally add a Pi comparison smoke (reusing
`scripts/tmux_pi_comparison_verify.sh` /
`scripts/tmux_transient_ui_verify.sh` and `pipy_harness.native.terminal_compare`)
that compares user-visible workflow semantics against `pi` in matching panes:
the `@` picker opening and ranking, `!`/`!!` shaded bash blocks, the model/thinking
footer changes on cycle hotkeys, the pending steering/follow-up region, and the
post-Escape `Operation aborted` frame. Exact Pi pixel/format matching is not the
hard gate; deterministic pipy conformance is. Sample active frames (timing-
dependent states like `Working...`, the streaming tail, the pending region, and
the abort frame) rather than relying on final screenshots.

Update `docs/harness-spec.md`, `docs/backlog.md` (Pi Gap Queue item 3 / Current
Largest Gaps item 3), `docs/pi-parity.md`, `README.md`, and this spec to match
shipped behavior, and get an independent review pass for the input-decoding,
provider-cancellation, and renderer slices.
