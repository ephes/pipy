# DF1 live smoke and dogfooding acceptance (2026-09-29)

Status: **partially accepted.** The daily-use bar holds on openai-codex after
four fixes made during the run. Other provider families were not tested live
because no credentials were available. Automatic compaction stops a session
when a single turn exceeds the context window. The larger gaps are listed as
follow-ons in [the backlog](../backlog.md#follow-ons).

- **Build:** branch `chore/df1-dogfooding`, based on `main` `731c8b87`.
- **Terminal:** tmux, 110x40, `TERM` from tmux, interactive TUI (the
  prompt-toolkit runtime).
- **Isolation:**
  - `PIPY_CONFIG_HOME`, `PIPY_SESSION_DIR`, `PIPY_NATIVE_SESSIONS_ROOT`,
    `PIPY_NATIVE_DEFAULTS_PATH`, `PIPY_NATIVE_THEME_PATH` and
    `PIPY_PROMPT_HISTORY_PATH` all pointed into a scratch directory.
  - Auth stayed on the default `~/.local/state/pipy/auth/openai-codex.json`,
    which was read but never copied or printed.
  - `~/.pipy/settings.json` and the real session archive were not touched.
- **Observation hook:** a scratch-only wrapper around
  `_codex_request_body`, never committed. It logged `model`, `reasoning`,
  whether a cache key was present, the input item count and the tool count
  for every Codex request. The same wrapper could inject an HTTP 503 into the
  next N transport attempts, which exercised the real retry path.
- **Project:** a throwaway 4-file Python package (`tinystats`) with a failing
  `median` test, a `CLAUDE.md` holding the test command and a codeword, and a
  project skill `.pipy/skills/release-notes`.
- **Spend:** about 60 Codex requests. Catalog pricing puts them at roughly
  $0.10. All ran on a ChatGPT subscription login, shown as `(sub)`.

## Provider smoke

`pipy repl --native-provider openai-codex --native-model <m> --thinking <l> -p "Reply with exactly: pong"`

| Family / model | Thinking | Wire `reasoning` | Result | Footer |
| --- | --- | --- | --- | --- |
| openai-codex `gpt-6-sol` | off | `{"effort":"none"}` | pass, `pong` | `$0.003 (sub) 0.5%/272k … gpt-6-sol • thinking off` |
| openai-codex `gpt-6-sol` | medium | `{"summary":"auto","effort":"medium"}` | pass | `… • medium` |
| openai-codex `gpt-6-sol` | max | `{"summary":"auto","effort":"max"}` | pass | `… • max` |
| openai-codex `gpt-6-luna` | low | `{"summary":"auto","effort":"low"}` | pass | `$0.000 (sub)`: 1.5k input at $0.10/M rounds to zero |
| openai-codex `gpt-6-luna` | max | `{"summary":"auto","effort":"max"}` | pass | `… • max` |
| anthropic opus/sonnet 5.5 | — | — | not live-verified (no credentials) | — |
| xai | — | — | not live-verified (no credentials) | — |
| github-copilot | — | — | not live-verified (no credentials) | — |
| amazon-bedrock, google, others | — | — | not live-verified (no credentials) | — |

Credential check: none of `ANTHROPIC_API_KEY`, `XAI_API_KEY`,
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`/`GITHUB_TOKEN`, AWS or Google variables is
set. The pipy auth store holds only `openai-codex.json`, and no interactive
logins were started.

The backend accepted every model ID and effort value. The prompt-cache key is
sent on every Codex request. On the second turn of a session the footer shows
`CH60.5%` (turn 1 `CH53.3%`, 2.2k of 2.4k new input read from cache), and after
a `/tree` branch the first request hit `CH91.4%`.

## Dogfooding scenarios

| # | Scenario | Result |
| --- | --- | --- |
| 1 | Real repository task: find and fix the failing test, run the checks from `CLAUDE.md` | **pass** (render bug fixed, see B1) |
| 2 | `CLAUDE.md` context pickup | **pass** |
| 3 | Skills listing | **pass** |
| 4 | Interrupt / steer mid-run | **pass** |
| 5 | Escape / Ctrl-C cancel mid-stream | **pass** after fix B3 |
| 6 | `/thinking` selector | **pass** |
| 7 | Manual `/compact`, then continuing | **pass** |
| 8 | Automatic compaction | **partial** (fires, but can wedge; F5) |
| 9 | Retry after provider failure | **pass** (UX gap F4) |
| 10 | Resume after exit (`-r`), `/resume` | **pass** for context; **fail** for UI and state (F2, F3) |
| 11 | `/tree` branch, `/fork` | **pass** (UI gap F2) |
| 12 | Model switch gpt-6-sol ↔ gpt-6-luna | **pass** after fix B2 |
| 13 | Cross-provider switch | **partial** (see below) |
| 14 | `!` shell | **pass** after fix B4 |

1. **Real repository task.** gpt-6-sol read `core.py`, `CLAUDE.md` and the
   test, edited `median` with the `edit` tool, and ran
   `python3 -m unittest discover -s tests` (4 OK). It then answered in two
   sentences. A second task added `variance()` with a test and export (5 OK).
   The edit diff corrupted the screen; that was fixed as B1.
2. **`CLAUDE.md` pickup.** The startup `[Context]` block lists `CLAUDE.md`.
   Asked for the project codeword, the model answered `PELICAN-42` without
   calling a tool. The instructions also steered the test command.
3. **Skills listing.** The startup `[Skills]` block lists the project skill
   `release-notes` (with `--approve` for project trust).
4. **Interrupt and steer.** While the variance task ran, Enter queued
   `Steering: …`. It was delivered mid-run: the test was renamed
   `test_variance_population` and gained the empty-input `ValueError`.
5. **Cancel.** Before the fix, the stream stopped at once but the frame froze
   with a stuck spinner. `Operation aborted` appeared 3.1 s later
   (27 sampled frames), because the WebSocket close handshake ran on the UI
   thread. After fix B3, the next sampled frame, 65 ms later, shows
   `Operation aborted`. Escape during `Working...` behaved the same way.
6. **`/thinking` selector.** It shows Pi's 7 levels and descriptions, with
   `✓ medium` and `· default` marked. Choosing `low` updates the footer, and
   the next request carries `effort: low`.
7. **Manual compaction.** `/compact` dropped 5 exchanges and kept 2 in about
   12 s. The semantic summary had the files, the median fix, the variance
   test name, the test command and the codeword. The next answer reproduced
   all of them with no tools and no re-explaining. The footer went from 1.2%
   to 0.7%.
8. **Automatic compaction.** Tested with `compaction.contextWindow: 12000` and
   `reserveTokens: 3000`. It fired after a `cat` of a 24 KB file
   (`compacted conversation context (auto; dropped 4 earlier exchange(s), kept 1)`),
   and the summary was accurate. The single retained turn was still over the
   window, so every later prompt was refused: `/compact` said
   `nothing to compact yet`, and only `/new` recovered. See F5, fixed on
   `fix/f5-oversized-turn`: the oversized turn is still refused, and the next
   prompt now compacts it away and is answered.
9. **Retry after provider failure.** One injected 503 was retried silently
   and the answer arrived. Four 503s (all attempts) waited 2, 4 and 8 s under
   `Working...` with no retry notice, then showed
   `provider failure during turn: OpenAICodexHTTPStatusError … 503`. The next
   prompt worked, so the session stayed usable. A real
   `OpenAICodexResponseParseError: stream returned an error event` also
   happened once during the run; it was not retried, and the session stayed
   usable. See F4.
10. **Resume after exit.**
    - `-r` opened the picker, and the named session `df1-dogfood` came first.
      Context was fully restored: the first request had 15 input items.
    - The screen showed no earlier conversation (F2).
    - The thinking level came back as `medium`, although the session's last
      `thinking_level_change` was `low` (F3).
    - `/resume` inside the REPL switched sessions and restored context
      (17 input items).
11. **`/tree` and `/fork`.**
    - `/tree` listed the 37 entries. Selecting the assistant turn before the
      variance work moved the leaf there, and the next answer correctly said
      variance did not exist yet.
    - `/fork` forked the current leaf into a new session file, which is
      pipy's documented bare-`/fork` behavior; Pi opens a message selector.
    - Both left the transcript area blank (F2).
12. **Model switch.** Before fix B2, `/model` from gpt-6-sol to gpt-6-luna
    sent only the new user message: 1 input item, and the model answered
    `UNKNOWN` to a question about the previous turn. After the fix,
    `/model openai-codex/gpt-6-luna` replays the conversation (14 input items,
    including gpt-6-sol's encrypted reasoning items). The backend accepts
    them, and luna answers with the remembered word and the earlier edit.
13. **Cross-provider switch.**
    - `/model fake` is refused because the fake provider has no tool calls.
      Before fix B5, the refusal still changed the footer to `thinking off`
      while requests kept `medium`.
    - No second real provider family has credentials, so a live
      cross-provider continuation is not verified. The D8 fake-transport
      replay tests remain the only evidence.
    - The B2 fix makes the live switch use the same history replay that a
      durable reopen under a different provider already exercised in D8.
14. **`!` shell.** `!ls tests` ran and its output was added to the context.
    The row read `$ $ ls tests` before fix B4.

## Bugs fixed on this branch

| ID | Bug | Fix |
| --- | --- | --- |
| B1 | The `edit`/`write` diff went raw to the error stream while the TUI held the terminal in raw mode, so the bare-LF diff staircased across the editor and footer. | In the TUI, `ToolContext.stderr_sink` now commits the diff as a transcript row (`TranscriptComponent.add_tool_side_output`). |
| B2 | Any model switch (`/model`, Ctrl+P, RPC `set_model`, extension `setModel`) cleared provider-visible history. The new model lost the conversation, but a later resume still had it. Pi's `setModel` keeps it. | `CodingSessionState.publish_model_mutation` keeps the messages. Contract docs, the E5 parity check and tests updated. |
| B3 | Escape/Ctrl-C froze the frame for about 3 s. `CancelToken.cancel` runs on the UI thread and called `websocket.close()`, which waits for the server's close frame. | A `_WebSocketCanceller` shuts the socket down (`SHUT_RDWR`) before `close()`, like the HTTP `_ConnectionCloser`. |
| B4 | TUI tool rows read `$ bash(command="…", timeout=120)` and `$ edit(new_string=…)`, and `!cmd` read `$ $ cmd`. | `bash` shows `$ <command> (timeout Ns)` (Pi's format), `edit`/`write` show the path, and the local shell passes the bare command. |
| B5 | A refused `/model` switch published the target's resolved or clamped thinking level, so the footer disagreed with the requests. | The refusal keeps the expected thinking level. The docs' "explicit `:level` is retained" compatibility note is removed. |

Excerpt of B1, before and after (plain text from the tmux pane):

```text
--- a/tinystats/core.py
───────────────────────+++ b/tinystats/core.py──────────────────────────────
/private/tmp/…/proj/tinyst@@ -16,5 +16,6 @@…
$0.000 (sub) 0.0%/272k (auto)                  raise ValueError("median() of empty da
ta")
         ordered = sorted(values)
                                      middle = len(ordered) // 2
```

```text
 $ edit tinystats/core.py
 --- a/tinystats/core.py
 +++ b/tinystats/core.py
 @@ -19,6 +19,7 @@
  def median(values: Sequence[float]) -> float:
 +    """Return the median of the values."""
      if not values:
 edited tinystats/core.py (1 replacement(s))
 $ python3 -m unittest discover -s tests (timeout 120s)
```

## Follow-ons found (not fixed here)

These are recorded with their repro steps in
[the backlog follow-ons](../backlog.md#follow-ons) under DF1.

- **F1 `read` truncates silently.** The `read` tool has no `offset`/`limit`,
  stops at 200 lines / 8 KB and adds no continuation notice. It refuses files
  over 256 KB and any file with secret-shaped content. Pi's `read` takes
  `offset`/`limit`, reads 2000 lines / 50 KB and appends
  `[Showing lines …]`. On a 901-line CSV the model believed it had read the
  whole file, and the auto-compaction summary recorded a wrong fact
  ("last row has ID 00198"). Fixed by READ1 (see the backlog's Done table).
- **F2 Resumed conversations are not rendered.** `-r`, `/resume`, `/tree`
  navigation and `/fork` leave the transcript empty; Pi renders the branch.
  Fixed by DF1-F2/F3 (see the backlog's Done table).
- **F3 Resume does not restore model or thinking level.** Pi restores the
  session's thinking level, and its model when the CLI gives none
  (`sdk.ts:197-245`). Fixed by DF1-F2/F3.
- **F4 Retry UX.** Provider retries are silent (up to about 14 s of
  `Working...`), while Pi shows `Retrying (n/3) in Ns`. A stream `error`
  event with an unknown status is not retried, and whether Pi would retry it
  depends on the error text, which was not captured. After a failed turn the footer context jumped from
  0.6% to 7.2% until the next success. Fixed by DF1-F4: retries show Pi's
  loader, Codex stream errors carry and are classified by their text, and
  the footer keeps the last successful context (see the backlog entry for the
  remaining differences).
- **F5 Compaction can wedge the session.** When the latest turn alone
  exceeds the window, every prompt is refused and `/compact` has nothing to
  do. Fixed by DF1-F5 (see the backlog's Done table); the remaining
  too-large-summary case is follow-on F5b.
- **F6 Aborted turns persist no assistant message.** An aborted turn leaves
  consecutive user messages and drops the partial text. Pi persists the
  aborted assistant message. Fixed by DF1-F6 (see the backlog's Done table);
  the remaining differences are follow-on F6b.
- **F7 TUI polish.**
  - Notices read `pipy  pipy: …`.
  - Non-bash tool rows keep the `$ ` prompt (`$ edit path`, `$ find …`);
    Pi shows `edit path`, `find X in Y` and `grep /X/ in Y`.
  - `/model <bare-id>` resolved `gpt-6-luna` to the unauthenticated `openai`
    row.
  - The `/model` selector lists unavailable models; Pi lists only models with
    configured auth.
  - Startup leaves a stale editor frame in scrollback.
  - `-p` prints the startup chrome and footer to stderr.
  - The manual compaction summary treated the summarize instruction as a
    user request ("the next asks for the combined context summary").
  Fixed by DF1-F7: notices, the stale startup frame and the `-p` chrome,
  with Pi's dark palette, the live compaction redraw and a ticking
  `Elapsed`. The tool-row prompt was fixed by TOOLS3. Model selection and the
  compaction summary prompt remain (follow-on F7b).

## Demo material

The terminal captures stay in the session scratchpad and are not committed
(`…/scratchpad/df1/caps/`):

- `02-task-done.log`: the full repository task (read → edit → tests → answer),
  with the B1 corruption visible.
- `04-steer.log`: steering delivered mid-run.
- `cancel-frames/` and `cancel-fixed-frames/`: 100 ms and 50 ms frame
  samples of Escape before and after B3.
- `07-session1-final.log`: the first session end to end, including manual
  compaction and the post-compaction recall.
- `08-autocompact.log`: automatic compaction and the F5 refusal loop.
- `10-fixed-diff-headers.log`: the B1 and B4 fixes live.
- `11-model-switch-fixed.log` and `12-switch-to-luna-keeps-history.log`:
  B5 and B2 live.
- `retry1-frames/` and `retry4-frames/`: the silent retry, then the surfaced
  failure.
