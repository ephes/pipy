# Parity-Prompt + Chrome Comparison (2026-05-26)

Two side-by-side runs of pipy and Pi, captured the same day:

1. **`comparison-prompt/`** — the substantive parity prompt from the
   goal (`docs/parity-criterion.md`). Proves pipy can inspect both
   workspaces through its model-driven tools and produce a fair
   comparison answer that caveats Pi's broader surfaces.
2. **`hello-world/`** — the chrome-parity smoke test. Proves the
   live REPL chrome (startup chrome, user-message bubble shape,
   assistant indent, bottom-frame layout, cursor visibility) matches
   pi at the visual level.

Both runs were launched from `/Users/jochen/projects/pipy` and used
the same provider/model so the comparison is honest.

## Harness commands

| Harness | Command | Notes |
| ------- | ------- | ----- |
| pipy (comparison prompt) | `uv run pipy repl --native-provider openai-codex --native-model gpt-5.5 --read-root /Users/jochen/src/pi-mono` | Normal product path. `--read-root` is required so the model-driven `read`/`ls`/`grep`/`find` tools can resolve absolute paths under `~/src/pi-mono`. Mutation tools always stay inside the workspace. |
| pipy (hello-world chrome) | `uv run pipy repl --native-provider openai-codex --native-model gpt-5.5` | Same product path, no `--read-root` (chrome test doesn't need cross-repo access). |
| pi (both runs) | `pi --provider openai-codex --model gpt-5.5` | **Installed `pi` binary at `/opt/homebrew/bin/pi` (pi v0.75.5).** `pi-test.sh` was the preferred reference per the goal, but `~/src/pi-mono/node_modules` is missing (no `tsx`), so the binary is the documented fallback. |

`--read-root` is documented in `src/pipy_harness/cli.py:241`; the
helper-text for each model-driven read tool (`tools/read.py:77`,
`grep.py:106`, `find.py:61`, `ls.py:65`) explains the read-only
sibling-repo boundary.

## tmux orchestration

Both runs used a dedicated `-L parity` tmux socket with
`history-limit 50000` and 220×60 panes:

```fish
tmux -L parity new-session -d -s pipy -x 220 -y 60 'exec fish'
tmux -L parity new-session -d -s pi   -x 220 -y 60 'exec fish'
# Launch harness in each pane, then send the prompt:
tmux -L parity send-keys -t pipy -l '<prompt>'; tmux -L parity send-keys -t pipy Enter
tmux -L parity send-keys -t pi   -l '<prompt>'; tmux -L parity send-keys -t pi   Enter
```

Logs captured with `tmux -L parity capture-pane -t <session> -p -e -J -S -50000`
once the `Working...` spinner cleared in both panes. The `-J` flag
preserves trailing whitespace, which the user-message bubble's
full-width padding rows depend on.

## Evidence

### `comparison-prompt/` — substantive answer parity

Prompt sent to both:

```text
what are the differences between pipy and pi in ~/src/pi-mono?
```

| File | What |
| ---- | ---- |
| `pipy-comparison.log` | Plain-text pipy transcript: startup chrome, tool calls, reasoning, final answer. |
| `pipy-comparison.ansi` | Same with ANSI escapes preserved. |
| `pipy-comparison.png` | Headless-Chrome screenshot of the pipy pane (via `scripts/tmux_screenshot.sh`). |
| `pi-comparison.log` | Plain-text pi transcript. |
| `pi-comparison.ansi` | Same with ANSI escapes preserved. |
| `pi-comparison.png` | Headless-Chrome screenshot of the pi pane. |

Both transcripts cover every axis the goal required:

- **Language/runtime** — Python 3.11+/uv vs Node 22+/npm.
- **Architecture** — `pipy_harness` + `pipy_session` packages vs
  `packages/{ai,agent,coding-agent,tui}`.
- **Tools** — `read`, `ls`, `grep`, `find`, `write`, `edit`,
  `edit-diff`, `truncate` registered; **`bash` deliberately not
  production-registered (B7).**
- **Providers** — Lists the 11 parity providers + OpenRouter bonus and
  explicitly states *Pi's user docs advertise a broader provider
  ecosystem*.
- **UI / TUI** — Calls out that pipy is *not a full TUI* and contrasts
  with Pi's `pi-tui` editor/footer/overlays/extension UI.
- **Sessions / storage** — Metadata-first JSONL archive with opt-in
  transcripts sidecar vs Pi's full session tree.
- **Extensibility** — Notes pipy *does not copy Pi's TypeScript
  extension/package ecosystem*.
- **Privacy posture** — Pipy avoids storing prompts/model text/tool
  payloads/secrets by default; Pi is designed for richer capture/sharing.
- **Bash gap** — Explicitly cites B7 as the remaining red parity row
  and explains it stays deferred until a real shell sandbox lands.

Neither transcript falls back to stale docs alone — both ran tool
calls (`ls`, `find`, `read`, `grep`) against both workspaces before
answering. Both explicitly acknowledge that the parity-score number is
not full product parity and call out Pi as the more complete product.

### `hello-world/` — chrome / visual parity

Prompt sent to both:

```text
hello world!
```

| File | What |
| ---- | ---- |
| `pipy-hello.log` | Plain-text pipy chrome capture. |
| `pipy-hello.ansi` | Same with ANSI escapes preserved (contains `48;2;52;53;65` user-message bg). |
| `pipy-hello.png` | Headless-Chrome screenshot of the pipy pane. |
| `pi-hello.log` | Plain-text pi chrome capture. |
| `pi-hello.ansi` | Same with ANSI escapes preserved. |
| `pi-hello.png` | Headless-Chrome screenshot of the pi pane. |

Chrome match (same cwd, same provider):

```
pi   [Context] ~/.pi/agent/AGENTS.md, ~/projects/AGENTS.md, AGENTS.md
pi   [Skills]  commit-ready, commit-workflow, review-handoff

pipy [Context] ~/.pipy/AGENTS.md,    ~/projects/AGENTS.md, AGENTS.md
pipy [Skills]  commit-ready, commit-workflow, review-handoff
```

The only remaining textual difference in `[Context]` is the global
path: `~/.pipy/AGENTS.md` vs `~/.pi/agent/AGENTS.md`. That is the
intended "pipy is its own product" boundary set by commit `e194179`.

### Parity-score artifacts

Two `just parity-score` runs are kept so the orthogonal deletion
track does not contaminate this chrome-parity evidence:

- `parity-score-pre-deletion.log` — **49 / 50** at commit `657b327`
  (HEAD when this chrome-parity slice was authored), only B7
  (`bash`) red. Captured by re-running `just parity-score` from a
  detached `git worktree` at HEAD.
- `parity-score-post-deletion.log` — **43 / 50** after the parallel
  code-quality cleanup track removed several feature modules
  (`docs/audit/2026-05-26/code-quality-audit/`, `docs/backlog.md`
  "Code Quality Audit Track"). Those deletions are not part of
  this chrome-parity slice; the drop reflects deliberate scope
  reduction in a separate track.

## Chrome fixes that landed for the `hello-world/` evidence

1. **chezmoi-managed `~/.local/share/chezmoi/dot_pipy/`** mirrors
   `dot_pi/`'s pattern: `AGENTS.md` + three skill symlinks pointing
   at `~/projects/agent-stuff/pi/skills/<name>`. `chezmoi apply
   ~/.pipy` materialises `~/.pipy/AGENTS.md` and
   `~/.pipy/skills/{commit-ready,commit-workflow,review-handoff}`.
2. **`workspace_context.resolve_global_instruction_root`** now
   prefers `~/.pipy/` (when present) over `~/.config/pipy/`, so the
   chezmoi-managed `~/.pipy/AGENTS.md` is actually composed into the
   model system prompt — not just listed in chrome.
3. **`chrome.py`** parent-walks `INSTRUCTION_CANDIDATE_FILENAMES`
   (the same tuple `discover_workspace_instructions` uses),
   collects ancestor labels root-most-first, and emits
   `[Context]` / `[Skills]` in the loader's order
   (global → ancestors → cwd). `~/projects/AGENTS.md` shows up in
   chrome where it was previously composed silently.
4. **`tool_loop_session.render_user_message`** clears the input-frame
   separator row above the panel and accounts for visual-row wrapping
   on narrow panes via `chrome_width`-based ceil-div. The
   user-message bubble is a three-row full-width strip
   (`#343541` = `48;2;52;53;65`) with top + bottom padding rows
   padded with spaces so `tmux capture-pane -e -J` preserves them
   in screenshots.
5. **`_handle_stream_chunk`** prefixes the assistant stream with one
   space and rewrites `\n` → `\n ` for subsequent lines, mirroring
   pi's 1-char left indent. `end_provider_turn` adds a trailing
   blank row so the bottom-frame separator sits one row below the
   response.
6. **`Working...` spinner** is no longer italic — just dim — to
   match pi.
7. **`_SlashMenuLineEditor`** drops the leading-space prompt when
   the prompt label is empty (cursor lands at column 1 like pi),
   paints a reverse-video space at the input column so a visible
   block stays even when the pane loses focus, and recognises
   `ESC + \r`/`ESC + \n`, xterm `modifyOtherKeys`, and kitty
   encodings as `shift-enter` (inserts `\n` into the buffer).
   `_render_bottom_frame` appends one trailing `\r\n` past the
   status row so a blank row sits between the status line and the
   pane edge.
8. **`scripts/tmux_screenshot.sh`** adds `-J` to `tmux capture-pane`
   so trailing whitespace cells survive, and includes `#343541` in
   the `panel_bgs` full-width-expansion list so the user-message
   bubble renders as a contiguous strip.

## Out-of-scope follow-ups

- **Esc-to-interrupt** during a running `provider.complete(...)` —
  needs a TTY cbreak watcher + provider-turn cancellation context.
  Pi cancels via `AbortController` in a single raw-mode session;
  pipy is line-oriented and leaves raw mode after each `read_line`.
- **Multi-line buffer rendering** after shift+Enter — the `\n`
  reaches the submitted prompt, but the slash-menu's `_render`
  doesn't yet display multi-line input pretty (only `\x1b[{col}G`
  cursor positioning, no row).
