# Visual Gaps After Round 1 (tmux side-by-side)

Source: `tmux/pi-tmux-1.log` and `tmux/pipy-tmux-1.log` (real PTY captures
of both products answering the same prompt). Updated 2026-05-26.

## Tool / output rendering

| Aspect            | Pi                                           | Pipy (today)                            |
| ----------------- | -------------------------------------------- | --------------------------------------- |
| Tool-call line    | `read docs/backlog.md:1-2000`                | `→ read(path="docs/backlog.md")`        |
| Bash-style prefix | `$ find ... (timeout 30s)`                   | n/a (no bash)                           |
| Result block      | Inline, two-blank-line spacing, no glyph     | `↳ ` prefix on every result line        |
| Long output       | `... (231 earlier lines, ctrl+o to expand)`  | `… (+N more line(s))`                   |
| Per-tool timing   | `Took 0.0s` / `Took 1.0s` under each result  | absent                                  |
| Spacing           | Blank line above and below each block        | tight, no blank lines                   |

## Reasoning / progress

| Aspect | Pi | Pipy |
| --- | --- | --- |
| Italic reasoning between tool calls | `Investigating pi-mono and pipy`, `I'm wondering why there was no output...` | none |
| Active spinner | `⠴ Working...` line, animated | none |
| Inline thought sections | titled `Assessing feature parity` blocks | none |

## Bottom status

| Aspect | Pi | Pipy |
| --- | --- | --- |
| Cwd label | `~/projects/pipy (main)` (tilde-home + git branch) | `/Users/jochen/projects/pipy` (absolute, no branch) |
| Status line | `↑61k ↓1.1k R42k $0.358 (sub) 21.1%/272k (auto) (openai-codex) gpt-5.5 • high` | `$0.000 (sub) 0.0%/25 (tools · turn 0) (openai-codex) gpt-5.5 • default` |
| Tokens | `↑in ↓out R<reasoning>` | `↑in ↓out` only, hidden until turn 1 |
| Cost | accumulated provider cost | always `$0.000` |
| Context window | `21.1%/272k (auto)` | tool-budget %, not context-window |
| Effort | `high` (auto-detected for codex+gpt-5.5) | hard-coded `default` |

## Behavior

| Aspect | Pi | Pipy |
| --- | --- | --- |
| Per-turn tool budget | none (model picks) | 25 (exhausted on this prompt) |
| Tool thrashing on dup reads | model converges | model re-reads docs/pi-parity.md and grep-thrashes once budget low |

## Priority ordering for the next gap-closure passes

1. **Tool-call rendering** (`read path:start-end`, drop `→ ↳`).
2. **Cwd with tilde-home + git branch** in the bottom status.
3. **Effort label**: detect from provider/model (`gpt-5.5` codex defaults to `high`).
4. **Context-window % instead of tool-budget %** in the bottom status.
5. **Spinner / `Working...` indicator** while the provider call is in flight.
6. **Per-tool timing** (`Took Ns`).
7. **Italic reasoning text** between tool calls (codex `reasoning.summary.delta`).
8. **Cost / token surface** during the run (codex usage events).
9. **Tool budget**: raise default and stop reporting it in the footer.
10. **Long-output truncation**: head+tail collapse with `(N earlier lines)` cue.
