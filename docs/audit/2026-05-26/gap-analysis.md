# Gap Analysis: Default `pipy` vs Default `pi` Interactive Behavior

Date: 2026-05-26

Target prompt: `where are we regarding feature parity compared to ~/src/pi-mono?`

## Captures (this directory)

- `pipy-default-before.log` — default `pipy` (no args) with the target prompt on stdin.
- `pi-print-before.log` — default `pi -p` (print mode) with the same prompt.

## Side-by-side summary

| Dimension                | Pi (today)                                                                        | Pipy (today)                                                                                 |
| ------------------------ | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Tool inspection          | Reads files, runs `just parity-score`, synthesizes the answer.                    | Refuses: "I'm constrained not to inspect files or run tools."                                |
| Streaming cadence        | Streams chunks of provider text live, incremental.                                | Whole final answer batched after EOF — no live tokens.                                       |
| Progress / thought style | Italic/dim "Working..." spinner + loader frames.                                  | None — just the bottom status footer.                                                        |
| Tool block rendering     | Styled inline blocks with title (`toolTitle`), body (`toolOutput`), background.   | Nothing visible. Tools never get called.                                                     |
| Command output styling   | Pi paints a dim/contrast background behind tool output.                           | N/A — never rendered.                                                                        |
| Cross-repo inspection    | Pi happily reads `~/src/pi-mono` and the current pipy workspace.                  | All tools enforce *workspace-relative* paths and refuse `~/src/pi-mono`.                     |
| Final answer quality     | "49/50 on the locked parity criterion. Only red row: B7 (`bash` not registered)." | Generic "I can't verify, here are the docs you should check yourself."                       |
| Safety boundaries        | Pi uses its own `bash-executor` with timeouts and `.git` rules.                   | Pipy keeps `bash` deferred (correct). Read-only inspection must stay within an allowed root. |

## Root causes (verified by reading source)

1. **System prompt forbids tool use.**
   `src/pipy_harness/native/session.py:2223` defines the only system prompt
   used by every adapter, including the tool-loop adapter:

   > "You are the native pipy runtime bootstrap. Complete exactly one minimal
   > provider turn and do not execute tools."

   The tool-loop REPL composes this verbatim through
   `compose_system_prompt(NATIVE_BOOTSTRAP_SYSTEM_PROMPT, discovery)` in
   `adapters/native.py:313`. The model behaves accordingly and never asks
   for tool calls.

2. **REPL provider call is not streamed.**
   `tool_loop_session.py:303` calls `self.provider.complete(provider_request)`
   without the `stream_sink` keyword. `openai-codex` and `fake` already accept
   `stream_sink`, but the REPL never plumbs one through.

3. **Tool invocations are invisible.**
   The loop in `tool_loop_session.py` runs tools silently. Nothing is printed
   between the model deciding to call `read` and the next provider call.
   Compare Pi's `tool-execution.ts` component, which paints a styled
   pending/completed block on every tool round-trip.

4. **All read-only tools refuse paths outside `workspace_root`.**
   `read_only_tool.py:_validate_workspace_relative_path` and the
   `_is_relative_to` check in each tool ensure the resolved candidate stays
   under the workspace. There is no concept of additional read-only roots,
   so `~/src/pi-mono` is unreachable for the parity-comparison flow even
   if the model tried.

5. **Tool budget is per-turn 10 and unsignaled.**
   Survivable, but a real parity audit reads more files than that. We will
   keep 10 as the default but raise the soft ceiling for the model-visible
   guidance, and surface a clear "tool budget exhausted" reminder when hit.

## Done criterion → required slice

- *Pipy must answer with a useful parity reading.* → Fixes #1, #4 (system
  prompt rewrite, reference-root tool boundary).
- *Pipy must stream incrementally.* → Fix #2.
- *Pipy must show Pi-like progress / tool / output blocks.* → Fix #3.
- *Pipy must not claim "I cannot inspect".* → Implied by #1 + #4 + actual
  tool execution.
- *Pipy must not expose unsafe model-visible bash.* → No change required;
  `production_tool_registry()` deliberately excludes `BashTool`. The
  reference-root expansion is read-only and reuses the existing
  `.git`/symlink/secret-content defenses.
