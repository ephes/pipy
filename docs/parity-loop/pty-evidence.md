# Parity-Loop PTY Evidence Recipes

Reference recipes for the parity loop's Phase 5 (`skill-body.md`): exercise
UI, cost, tool-call, and session behavior in a real tmux PTY without live
credentials and without touching `~/.pipy`, plus cheap live-model checks for
behavior only a real model shows.

## Isolation

- Point `HOME`, `PIPY_CONFIG_HOME`, `PIPY_SESSION_DIR`,
  `PIPY_NATIVE_SESSIONS_ROOT`, and `XDG_STATE_HOME` at scratch directories.
  Native session files do not use `PIPY_SESSION_DIR`; they follow
  `PIPY_NATIVE_SESSIONS_ROOT`.
- Gates and tests follow the same rule: any new gate or test sets a temp
  `PIPY_CONFIG_HOME`. `scripts/parity_checks/theme_behavior.py` wrote the real
  `~/.pipy/settings.json` on every run until commit `52838467`.
- The scratchpad is shared across concurrent agents: keep scripts and evidence
  in a slice-named subdirectory, and never overwrite another slice's helper
  script.

## Colour in tmux

The shared tmux server environment can carry `NO_COLOR=1` and `TERM=dumb`
(`tmux show-environment -g`). pipy captures then come out colourless while Pi
still colours, and a background comparison silently compares nothing. Launch
evidence sessions with
`env -u NO_COLOR TERM=xterm-256color COLORTERM=truecolor`, and check that the
`.ansi` capture contains ESC before comparing. A `tmux capture-pane -e` capture
omits blank cells, so compare row backgrounds only on non-blank rows. To get
Pi's exact colours for comparison, see "Rendering Pi's own code under Node" in
`porting-notes.md`.

## Running PTY gates

PTY gates such as `scripts/parity_checks/tui_workflow_conformance.py` can hang
in an uninterruptible state on macOS. Always run gates under `timeout 600`, and
rerun a hung or failed gate once before investigating it.

## Side-by-side tmux scripts

- `tmux -t` falls back to a session-name prefix when no session has the exact
  name: once `q-pi` has exited (or was already cleaned up),
  `tmux kill-session -t q-pi` kills `q-pipy` and silently loses the pipy
  capture. Name sessions so neither is a prefix of the other (`pi-<tag>` /
  `pipy-<tag>`), and target panes as `=name:` for `capture-pane` and
  `send-keys`.
- `send-keys` needs `-l --` for text that starts with `-`.
- Pause after Escape before the next key, or both apps read an Alt sequence.
- Pi's argument popup takes Enter to apply the completion without submitting,
  so `/model <text>` plus Enter does not run the command while the popup is
  open: send Escape, pause, then Enter.

## Scrollback artefacts: record the bytes

A `capture-pane` frame can show rows the terminal already erased. To diagnose
an inline-TUI scrollback artefact, record the exact bytes (run pipy under
`script -q <file>` inside tmux), replay them into a fresh pane with `cat`, and
read `tmux display -p '#{cursor_x},#{cursor_y}'`. F7's "stale editor frame" was
a paint before `TerminalUi.start()` plus tmux scroll-on-clear: `ESC[J` issued
from row 0 pushes the screen into history. The fix deferred paints until the
startup rows exist (Pi renders on `nextTick`); the erase sequence was fine.

## Fake providers

- The REPL fake runs as the synthesized row `fake/fake-tools` and reports no
  usage. For thinking UI, set
  `modelOverrides.fake-native-bootstrap.reasoning=true` in the isolated
  `models.json`.
- The fake providers never emit tool calls.
- `fake/fake-tools` (`AutomationFakeProvider`) waits for the turn's cancel
  token when a prompt starts with `BLOCK`; `STREAMBLOCK` first streams
  `PARTIAL:streamed-before-abort`, so Escape leaves an aborted turn with
  partial text to check live and after `pipy -r`. Use it for any
  abort-with-partial-text check: real adapters other than `openai-codex` send
  `stream: false`, so the completions stub below cannot produce partial text.

## Local completions stub (tool calls, usage, cost)

Register a custom `openai-completions` provider in the isolated `models.json`
whose `baseUrl` points at a ~60-line stdlib HTTP stub. pipy's
`openai-completions` adapter sends `"stream": false`, so the stub returns one
non-streaming chat-completions JSON body per request. Script it to:

- emit `tool_calls` to drive real tool execution in tmux;
- return `usage` (and give the stub's model entry a `cost` block; a
  provider-level `cost` is ignored) to check a non-zero
  cost point: compare the footer and `--mode rpc` `get_session_stats` against
  the exactly computed value.

## Cheap live-model checks

When a check needs a real model, for example to show that the model follows a
truncation notice, run `--mode json` with isolated `PIPY_CONFIG_HOME` and
session directories. Auth stays on the default store. A run costs cents and
gives a tool-call trace. Models often sidestep the path under test: in TOOLS1
the model piped `python3 gen.py` through `sed` itself instead of hitting the
cut. Pin the exact tool call in the prompt, for example "the command string
must be exactly X; never run it twice".

## Restored-session evidence (`pipy -r`)

A model restore can only be shown without a CLI model flag, because Pi
reapplies `--model` on every runtime:

1. Create the session with `--native-provider fake`.
2. Switch with `/model openai/gpt-5.5` under `OPENAI_API_KEY=test-only` (no
   request is sent).
3. Reset `PIPY_NATIVE_DEFAULTS_PATH` to a defaults file naming the fake
   provider, then run `pipy -r`.

Real Pi can be compared offline: build the session with Pi `SessionManager` in a
small `.mts` script, then open it with
`node --import ./packages/coding-agent/src/experimental/source-resolver.ts
packages/coding-agent/src/cli.ts --session <file>` from `~/src/pi-mono`, with
`PI_CODING_AGENT_DIR` pointed at scratch.
