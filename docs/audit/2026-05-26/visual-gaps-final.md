# Final Visual Parity Audit (after iterative gap closure)

Date: 2026-05-26

This document closes the side-by-side audit started in
`visual-gaps.md`. Each gap from the earlier doc is either implemented
or recorded as an intentional product difference.

## Gaps closed

| Gap                                  | State       | Where |
| ------------------------------------ | ----------- | ----- |
| Tool-call rendering (`read x:1-N`)   | Implemented | `tool_loop_session._format_pi_call_header` |
| Cwd label `~/projects/pipy (main)`   | Implemented | `tool_loop_session._friendly_cwd_label` |
| Effort label `high` for gpt-5.5      | Implemented | `tool_loop_session._effort_label_for` |
| Context-window % from real usage     | Implemented | `tool_loop_session._UsageAccumulator` |
| Per-tool `Took Ns` timing            | Implemented | renderer call site |
| Animated `Working...` spinner        | Implemented | `_ToolLoopRenderer.show_working` (thread) |
| Reasoning text between tool calls    | Implemented | `_ToolLoopRenderer.handle_reasoning_chunk` |
| Reasoning bold titles (`**X**`)      | Implemented | `_split_reasoning_segments` |
| Cost and token surface               | Implemented | `_UsageAccumulator`, `_pricing_for` |
| Tool budget raised (50/200)          | Implemented | `NativeToolReplSession` defaults |
| `... (N earlier lines, ctrl+o)`      | Implemented | `render_tool_result` |
| `read resource <abs>` prefix         | Implemented | `_format_pi_call_header` |
| `(ctrl+o to expand)` annotation      | Implemented | `_format_pi_call_header` |
| Read line range from `offset`/`limit`| Implemented | `_read_range_label` |
| Drop `assistant > ` stream prefix    | Implemented | `_handle_stream_chunk` |
| Startup chrome wording (`escape interrupt`, `! bash`, ctrl+o cue, tagline) | Implemented | `chrome.print_startup_chrome` |
| Startup chrome spacing (leading blank, trailing blank after [Skills]) | Implemented | `chrome.print_startup_chrome` |

## Documented intentional differences

These reflect deliberate product-level choices, not chrome gaps.

| Aspect                  | Pi                                       | Pipy                                                  | Why                                                       |
| ----------------------- | ---------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------- |
| Default shell tool      | `$ <command>` bash with timeout cue      | No bash; uses `ls/read/grep/find` tool calls          | Pipy keeps `bash` deferred until a real shell sandbox.    |
| Input prompt glyph      | Blank line (ink cursor)                  | `> ` (readline / slash-menu)                          | Pipy is line-oriented, not an ink-style alternate-screen TUI. |
| `ls` body shape         | Bare filenames                           | `file <path>` / `directory <path>`                    | Pipy's `ls` tool tags entries; deliberately more informative than bash `ls`. |
| Footer redraw cadence   | Anchored at terminal bottom, redraws live | Re-emitted between turns                              | Pipy does not own a fixed-position viewport; redrawing per token would scroll the screen. |
| Tagline wording         | `Pi can explain its own features…`       | `Pipy can explain its own features…`                  | Product name. Style and structure match.                  |
| Reference root surface  | Implicit                                 | `read resource /abs/path` + auto-detect or `--read-root` | Pipy exposes a safe read-only sibling-repo boundary explicitly. |

## Done definition

Both products, given the same prompt, now:

- show identical startup chrome shape (title row, controls strip,
  ctrl+o hint, tagline, [Context]/[Skills] sections, separator-framed
  input area, two-row bottom block).
- stream the same kind of reasoning prose between tool blocks.
- render tool calls as `<tool> <args>` (or `read resource …` for
  absolute paths), with `... (N earlier lines, ctrl+o to expand)`
  truncation and `Took {n}s` timing.
- animate a Pi-shape `⠋…⠏` Working spinner during provider calls.
- track `↑in ↓out R<reasoning>`, `$cost`, `used%/272k (auto)`, and
  `(provider) gpt-5.5 • high` in the bottom status.

The remaining product-level differences above are intentional and
documented. Closing them would require implementing arbitrary `bash`
behind a real sandbox or rewriting pipy on an ink-equivalent TUI;
both are out of scope for the live REPL visual-parity slice and live
explicitly in `docs/backlog.md`'s deferred boundaries.

## Captures

- `tmux/pi-paired.log` / `tmux/pipy-paired.log` — paired prompts
- `tmux/pipy-startup-v4.log` — final pipy chrome
- `tmux/pi-startup.log` — Pi chrome for comparison
- `tmux/pipy-tmux-after-refinement.log` — pipy full session
- `tmux/pi-tmux-final.log` — Pi full session
