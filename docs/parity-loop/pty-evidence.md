# Parity-Loop PTY Evidence Recipes

Reference recipes for the parity loop's Phase 5 (`skill-body.md`): exercise
UI, cost, tool-call, and session behavior in a real tmux PTY without live
credentials and without touching `~/.pipy`.

## Isolation

- Point `HOME`, `PIPY_CONFIG_HOME`, `PIPY_SESSION_DIR`,
  `PIPY_NATIVE_SESSIONS_ROOT`, and `XDG_STATE_HOME` at scratch directories.
  Native session files do not use `PIPY_SESSION_DIR`; they follow
  `PIPY_NATIVE_SESSIONS_ROOT`.
- The scratchpad is shared across concurrent agents: keep scripts and evidence
  in a slice-named subdirectory.

## Fake providers

- The REPL fake runs as the synthesized row `fake/fake-tools` and reports no
  usage. For thinking UI, set
  `modelOverrides.fake-native-bootstrap.reasoning=true` in the isolated
  `models.json`.
- The fake providers never emit tool calls.
- `fake/fake-tools` (`AutomationFakeProvider`) waits for the turn's cancel
  token when a prompt starts with `BLOCK`; `STREAMBLOCK` first streams
  `PARTIAL:streamed-before-abort`, so Escape leaves an aborted turn with
  partial text to check live and after `pipy -r`.

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
