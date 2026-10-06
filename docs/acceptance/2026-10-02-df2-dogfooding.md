# DF2 dogfooding — 2026-10-02

Status: **partially accepted on openai-codex (continued 2026-10-06)**. The
2026-10-02 run was blocked by HTTP 401 and `OpenAICodexOAuthError`. After
`pipy auth openai-codex login` the live scenarios ran; see
[Continuation 2026-10-06](#continuation-2026-10-06). Two bugs and three
smaller gaps were found; B1 and B2 are fixed (see below).

## Build and isolation

- Clean `main` at `214edc0d`, seven commits after v0.3.0.
- Installed via `uv tool install .`; installed version string remains 0.3.0.
- tmux TTY, 120 columns by 42 rows, truecolor, `NO_COLOR` removed.
- Scratch settings, archive, native sessions, defaults, theme and prompt history
  paths; `XDG_STATE_HOME` also points to scratch. Native sessions use
  `PIPY_NATIVE_SESSIONS_ROOT`, not the metadata archive root.
- `PIPY_AUTH_DIR` points to the existing auth directory. Credentials were not
  printed or copied. The failed refresh stored no new credential.
- SHA-256 checks before/after confirm real `~/.pipy/settings.json` and
  `~/.local/state/pipy/native-theme.json` unchanged.
- Raw captures and isolated session state remain outside Git in this Codex
  chat's `work/df2/` directory. Only this summary is durable project evidence.

## Task

Read implementation and existing usage/session tests; correct the stale
`docs/providers.md` claim that totals reset on model selection or resume;
run focused tests. The existing CHANGELOG describes persisted usage.
The live model never reached a tool call, so the task and correction remain open.

## Coverage

| Scenario | Evidence | Result |
| --- | --- | --- |
| Installed startup, openai-codex/gpt-6.1-sol | Header and selected model shown | pass |
| Real read/edit/tests task | First request HTTP 401, no tools run | blocked |
| Failure leaves input usable | Slash commands work after HTTP 401 | pass |
| `/thinking` | Searchable selector opens; low selected via command | UI pass; request effort unverified |
| `/model` | Selector opens with Codex models; current model selectable | UI pass; successful continuation unverified |
| `-r` without model/thinking flags | Picker opens; transcript, gpt-6.1-sol and low restored | pass for failed-turn session |
| Escape during running model/tool | No successful live run available | pending |
| Manual/automatic compaction and recall | No successful live history | pending |
| `/skill:df2-recall` | Scratch skill prepared but not invoked | pending |
| Cost footer | Shows `$0.000 (sub)` after failed call and resume | zero-cost display only |
| `/session` | Named file, messages and zero usage shown | UI pass; nonzero totals pending |

This is not a substitute for live daily-use acceptance. Resume evidence covers
state restoration after a failed turn, not semantic memory after useful work.
No Anthropic coverage was requested.

## Continue after login

Use the same isolated environment and installed build. Resume the session,
repeat the real task, exercise Escape during a running request/tool, create
sufficient history for compaction and verify the earlier task and codeword
survive it. Invoke the scratch recall skill, check nonzero usage against stored
messages, then resume again without CLI model/thinking overrides.
Only after these checks should demo recordings be treated as the DF2 baseline.

Separate measurement: `uv run --frozen pytest --collect-only -q` collected
7,437 tests. No full-suite pass is claimed by this count.

## Continuation 2026-10-06

- **Build:** `main` at `a992b43d` (docs-only after `214edc0d`), run from the
  checkout venv (`pipy 0.3.0`), against a detached scratch worktree of the
  same commit. tmux 120x42, truecolor.
- **Isolation:** `PIPY_CONFIG_HOME`, `PIPY_SESSION_DIR`,
  `PIPY_NATIVE_SESSIONS_ROOT`, `PIPY_NATIVE_DEFAULTS_PATH`,
  `PIPY_NATIVE_THEME_PATH`, `PIPY_PROMPT_HISTORY_PATH` and `XDG_STATE_HOME`
  in scratch; `PIPY_AUTH_DIR` on the real auth directory (read only). SHA-256
  of `~/.pipy/settings.json` and `~/.local/state/pipy/native-theme.json`
  unchanged after the run.
- **Spend:** about 15 Codex requests, `$0.083 (sub)` by catalog pricing.

| Scenario | Evidence | Result |
| --- | --- | --- |
| Real read/edit/tests task (gpt-6.1-sol, medium) | Read `session_usage.py`, footer and three usage test files; edited both stale `docs/providers.md` claims; ran the focused tests (92 passed) | pass; the fix is applied on `main` |
| Escape during a running `bash` tool | `sleep 60` row shows `tool cancelled by escape`, `Took 3.3s`; child process reaped | pass |
| Escape between final text and completion event | Visible answer stored as `stop_reason: aborted` with text and zero usage | matches Pi (abort before `done`) |
| Manual `/compact` after two user prompts | `nothing to compact yet.` at ~27k context | documented deviation (gap G1) |
| Manual `/compact` after three prompts | Summary kept codeword, files, edits and test result | pass, with bugs B1/B2 |
| `/skill:df2-recall` after compaction | `codeword: TANGERINE-42` and correct task line | pass |
| `/model openai-codex/gpt-6-luna` then a continuation | Switch and correct one-sentence answer from luna | pass, with gap G2 |
| `/session` | 37 messages, 5 user; tokens, cache share, per-model cost, `Cache Re-billed` | pass |
| `-r` without model/thinking flags | Picker, `[compaction]` row then kept messages redrawn, `gpt-6-luna • medium` restored, totals kept, codeword recalled | pass, with gap G3 |
| Automatic compaction | Not exercised (needs ~255k context); DF1 has live evidence | not repeated |

### Bugs

- **B1, `tokensBefore` holds bytes.** Manual compaction passes
  `work.cut.bytes_before` as `measure_before`
  (`native/repl/provider_selection.py:1452`), which becomes the compaction
  entry's `tokensBefore` (line 1840), the RPC `tokensBefore` and the TUI row
  `Compacted from 89,689 tokens` while the footer showed about 27k context
  tokens (9.9% of 272k). Pi stores `estimateProjectedContextTokens(...)`
  (`core/compaction/compaction.ts:897`).
- **B2, no indicator while compacting.** The summary request took about 17 s
  with nothing on screen; `compaction_start` reaches only the automation
  observer (`native/repl/wiring.py:1901`). Pi shows
  `Compacting context... (esc to cancel)`
  (`modes/interactive/components/status-indicator.ts:90`).

Both fixed on 2026-10-06 and checked live: the loader appears during `/compact`,
and the rows read `Compacted from 1,660 tokens` and, after an earlier
compaction, `1,978 tokens`, in line with the footer's 0.6% of 272k.

### Gaps and observations

- **G1:** manual `/compact` keeps two whole user groups, so one long task in
  a short session cannot be compacted; Pi keeps `keepRecentTokens` (20k) and
  can cut inside a turn. Documented in `docs/compaction.md` as inactive.
- **G2:** `/model <provider/id>` typed and submitted at once opened the fuzzy
  selector prefilled with the reference instead of switching; typed slowly,
  the first Enter was consumed (argument completion) and the second switched.
  Needs a deterministic repro and a Pi comparison before calling it a bug.
- **G4:** `pipy --model openai-codex/gpt-6-luna` started on the default
  `gpt-6.1-sol`: argparse prefix matching reads `--model` as `--models`
  (scoped-model patterns), with no error. Pi's `--model` selects the model;
  pipy spells it `--native-model`.
- **G3:** after resume the footer shows `0.0%/272k` context until the next
  response; Pi derives it from the last stored assistant usage.
- The footer kept `$0.000 … 0.0%` for the whole multi-request first turn and
  updated only at turn end. Not compared with Pi yet.
- Tests run by pipy's `bash` tool inherit `PIPY_NATIVE_SESSIONS_ROOT`; one
  RPC test wrote its session into the scratch root. Test fixtures isolate
  HOME but not this variable.
- After the trust prompt, two editor border lines are drawn above the
  startup header.
