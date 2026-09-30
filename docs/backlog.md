# Pipy backlog

Status: sole active task index, rewritten 2026-09-29.

## Status

- **D0–D8 daily-use program: complete** (2026-09-09). It covered semantic
  compaction, product sessions, request budgets, retry, shared queue and RPC
  controls, RPC bash and cross-provider replay.
- **Pi realignment to `4df157433`: done** (2026-09-29). MC1, CTX1, PC1, PC2,
  UX1, MC5, COST1, PR1/PR2, the gate and symlink repair, and DH1 landed; see
  [Done](#done-2026-09-29).
- **Remaining queue:** DF1 is partially done (openai-codex live; see its
  row in [Done](#done-2026-09-29)). READ1, the most important follow-on DF1
  found, is done. RL1 prepared release 0.2.0, so the active queue is empty;
  pick the next item from [Follow-ons](#follow-ons).
- **Tests:** `uv run --frozen pytest --co` collects 6,614 tests on
  `release/0.2.0` (6,576 on `chore/df1-dogfooding`).
- **Not verified live.** openai-codex has live evidence for `gpt-6-sol` at
  off/medium/max and `gpt-6-luna` at low/max: effort values, footer, cache hits,
  a real repository task, steering, cancel, manual and automatic compaction
  (semantic summaries were accurate), retry, resume, fork and a switch between
  the two models (DF1). Anthropic 5.x, xAI, Copilot, Bedrock and Google have no
  credentials here. They and cross-provider answer quality are still covered by
  fake-transport tests only.
- **Pi comparison gates:** `session_tree_pi_comparison.py` and
  `automation_pi_comparison.py` pass against Pi (the latter since SYS1a).

This document owns direction, order and task IDs. A queue item is a bounded
work order. It does not override a current runtime contract (see
[Contract map](#contract-map)). The per-slice D0–D8 narrative, the contracts
and the review evidence are archived in
[archive/backlog-2026-09.md](archive/backlog-2026-09.md).

## Evidence baseline

The queue below was baselined against these checkouts on 2026-09-29:

| Repository | Commit | Notes |
| --- | --- | --- |
| pipy | `23fdc99b` | `main`, clean (re-baselined by DH1) |
| pi-mono | `4df157433` | `packages/coding-agent` 0.87.1 plus Unreleased |
| tau | `15ca794` | latest tag `v0.4.5` |

Pi paths below are relative to `~/src/pi-mono` and tau paths to `~/src/tau`.
Pipy paths are relative to `src/pipy_harness/native/` unless they start with
`docs/`, `tests/` or `scripts/`. Line numbers are as of the baseline.

## Working rules

- **Trunk-based, one writer.** Work happens on `main` with one writer in the
  shared checkout, and each chunk is committed before the next starts
  (`AGENTS.md`). Read-only investigators can run concurrently. They report
  findings and do not edit this index.
- **Checks.** Run focused tests plus `just check`, and run `prek` when
  `.pre-commit-config.yaml` exists. Add `just docs-build` for doc changes and
  the PTY smoke tests for terminal changes. Test fixtures isolate HOME, so do
  not wrap `uv` or `just check` in a temporary HOME.
- **Review.** Use a fresh read-only reviewer that is separate from the
  implementer. The default is **GPT-6 Sol** through the `codex-review-loop` or
  `pi-review-loop` skill. Prefer a different-family reviewer when the change
  needs different-family evidence, and never present a same-family review as
  one. Cycles follow the **diminishing-returns rule** in `AGENTS.md` (Review
  budget, `eb851ce`), which has no fixed round cap:
  - Docs and plans can close on a clean first round.
  - Code continues only while rounds produce new, material findings.
  - Stop on self-inflicted churn.
  - Correctness defects in shared mutable state are exempt from every stopping
    rule.
  - Unresolved Critical/Warning findings block the commit.
  - Advisory findings are never reported as `CLEAN`.
  - The parity-loop workflow keeps its stricter different-family CLEAN gate.
- **Docs travel with behavior.** Each item updates its user docs, its topic
  spec and `CHANGELOG.md` in the same commit.
- **No deprecation shims.** Realign pipy-only surfaces to Pi directly.
- **Privacy is not a reason to diverge from Pi.** The metadata-only workflow
  archive stays separate from the full-content product session tree.

## Active queue

The queue is empty. Promote a follow-on into a numbered item here when a user
need makes it matter. Effort: S ≈ one focused slice, M ≈ two or three slices.

## Done (2026-09-29)

Each slice follows Pi `4df157433`. Plans are in `docs/specs/2026-09-29-*`, and
the per-slice notes (evidence, live checks, deviations) are archived verbatim
in [archive/backlog-2026-09-29-slices.md](archive/backlog-2026-09-29-slices.md).

| ID | Change | Merge |
| --- | --- | --- |
| MC1 | Current model rows, provider defaults and thinking maps | `60d9de59` |
| CTX1 | Pi context-file and skill discovery | `1235bedc` |
| Gates | Seven stale parity gates repaired; skills and context files follow symlinks; global resources under `~/.pipy` | `2c97afe5` |
| PC1 | OpenAI/Codex/Azure prompt-cache affinity, `PIPY_CACHE_RETENTION` | `c638b675` |
| PC2 | Anthropic/Bedrock `cache_control`, 1h retention | `f731231c` |
| UX1 | `/thinking` selector; footer shows the live level | `ca2de136` |
| MC5 | `just catalog-drift` against Pi's generated catalog | `208e5a47` |
| PR1/PR2 | `xai` and `github-copilot` providers, `/login github-copilot` | `ca883f31` |
| COST1 | Catalog-cost pricing for every model (Pi `calculateCost`) | `23fdc99b` |
| DH1 | Pi comparison gates run on `node` again and fail when Pi is missing; explicit `fake-native-bootstrap` REPL crash fixed; docs re-baselined | `chore/dh1-hygiene` |
| DF1 (partial) | Live smoke and dogfooding on openai-codex, recorded in [the acceptance note](acceptance/2026-09-29-df1-dogfooding.md). Five bugs were fixed: a model switch kept no history, edit/write diffs corrupted the TUI, Escape froze the frame for about 3 s on the WebSocket close, tool rows showed argument dumps, and a refused switch changed the footer's thinking level. Gaps: no live evidence for other families (no credentials), automatic compaction can stop a session (DF1-F5), and follow-ons below | `chore/df1-dogfooding` |
| READ1 | `read` follows Pi: `offset`/`limit`, 2000 lines / 50 KB, `[Showing lines …] Use offset=N to continue` notices; no size cap or content refusal. Follow-ons READ2, READ-IMG, TOOLS1 | `fix/read1-read-tool` |
| RL1 | Release 0.2.0: version bump, CHANGELOG `[0.2.0] - 2026-09-29` with highlights, the wheel ships `CHANGELOG.md`, README/quickstart checkout-install note (no PyPI package). The release tag is applied on `main` after the merge | `release/0.2.0` |
| TOOLS1 | `bash`, `grep`, `find` and `ls` follow Pi's output handling: `bash` keeps the last 2000 lines / 50 KB and names a full-output temp file; `grep` (regex, `glob`, `ignoreCase`, `literal`, `context`), `find` (fd glob rules) and `ls` take a `limit` and end cut output with Pi's `limit=2N` / `50.0KB limit reached` notices. `grep` uses `rg` or a Python fallback; `find` walks in Python. Follow-on TOOLS2 | `feat/tools1-tool-output-parity` |
| DF1-F5 | An oversized turn no longer wedges the session. Summary requests cut each tool result to 2000 characters (Pi `serializeConversation`), and a persistent session's automatic cut that keeps only the new prompt writes a compaction entry that keeps no earlier entry (Pi `firstKeptEntryId ?? id`). The oversized turn is still refused (as in Pi); the next prompt recovers. Follow-on F5b | `fix/f5-oversized-turn` |
| DF1-F2/F3 | Resume shows and restores the session ([plan](specs/2026-09-29-f2-f3-resume-restore-plan.md)). Startup `-r`/`--continue`/`--session`, `/resume`, `/tree` navigation, `/fork`, `/clone`, `/new` and `/import` redraw the transcript from `build_context_entries()` (Pi `renderInitialMessages`), clearing the scrollback like Pi; `[compaction]`/`[branch]` rows and plain tool results follow Ctrl+O. A new branch records `model_change` and `thinking_level_change` with its first message, every model switch records `model_change` (and a clamped level), and opening a session restores its model and thinking level unless the CLI pins them (Pi `core/sdk.ts:194-263`). Deviations: model restore needs a `model_change` (pipy assistant messages name no model); a runtime fallback keeps the live model; `/new` keeps the live model and level. Follow-on DF1-F2b | `fix/f2-f3-resume-restore` |
| SYS1a | Pi system messages in the transcript ([plan](specs/2026-09-29-sys1-system-messages-plan.md)). The first run records `role: "system"` with the prompt as the `preamble` section and every tool in `toolsAdded`; later runs and in-run tool changes record only changes. JSON/RPC events, `agent_end.messages`, `get_messages`, the session file and the compaction checkpoint (`systemMessage`) carry it; the TUI draws nothing, `/tree` shows `[system]`. Provider requests are unchanged (Pi's collapse path). `automation_pi_comparison.py` is green. Remainder: SYS1b | `feat/sys1-system-messages` |
| DF1-F6 | Aborted and failed turns are kept ([plan](specs/2026-09-30-f6-aborted-turn-plan.md)). The assistant message stores the streamed partial text and `stop_reason` `aborted`/`error` (with `error_message`), is persisted and shown on resume as `Operation aborted` / `Error: …`, and is skipped by every provider request (`materialize_provider_request`, Pi `transformMessages`) and by the summary request. JSON/RPC messages carry `stopReason`/`errorMessage`. Deviations: no partial thinking or tool calls stored; follow-on DF1-F6b | `fix/f6-aborted-turn` |
| USAGE1 | Usage stored on every assistant message ([plan](specs/2026-09-30-usage1-message-usage-plan.md)). The loop records Pi's `usage` (uncached `input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`, `cost` parts from `calculateCost`) and the answering `provider`/`model`; the session file and every JSON/RPC assistant message carry them. The footer, RPC `get_session_stats` and `/session` sum every stored assistant message on every branch (Pi `getSessionStats`), so totals survive resume and a model switch. The footer follows Pi's parts (uncached `↑`, latest-message `CH`, `formatTokens`); `/session` prints Pi's `Session Info` with `Messages`, `Tokens` and `Cost` (per-model breakdown, `Cache Re-billed`). Old sessions load unchanged. Follow-on USAGE1b | `feat/usage1-message-usage` |
| READ2 | `read`, `ls`, `grep`, `find`, `@file` and `@image:` resolve paths like Pi's `resolveToCwd`/`resolveReadPath` ([plan](specs/2026-09-30-read2-path-policy-plan.md)): cwd-relative or absolute, `~`, `@` prefix, unicode spaces, macOS screenshot/NFD/curly-quote variants, no deny list. `grep`/`find` leave out only what `rg --hidden`/`fd --hidden` do (measured rules in `tools/ignore_walk.py`, differential tests against the binaries). `--read-root`, `PIPY_READ_ROOTS` and the `Reference roots` prompt block are removed. No documented security boundary covers the read tools (`bash` is a real shell); the allowlisted command sandbox keeps its own policy. Follow-on READ2b | `feat/read2-path-policy` |
| TOOLS2 + READ2b | `write`, `edit` and `bash` follow Pi ([plan](specs/2026-09-30-tools2-write-edit-bash-plan.md)). `write`/`edit` resolve like `resolveToCwd` with no deny list or size cap; `write` makes parent directories and overwrites; `edit` takes `edits[]` with Pi's exact/fuzzy matching, BOM/CRLF handling, error texts and numbered diff (jsdiff `diffLines` ported); one file's mutations are serialized. `bash` ends a failure with `Command exited with code N` / `timed out` / `aborted` as an error. The `!` shortcut uses Pi's executor output (sanitized, 2000 lines / 50 KB, temp file), `bashExecutionToText` record and component rows. Pi's grep/find/ls/write/edit headers; `ls` sorts by a Node-measured ICU approximation; the global git excludes file applies to the Python walks. No documented boundary covers the mutation tools. Deviations: the no-terminal `!` keeps a 600 s bound; collation outside the measured table; follow-on TOOLS3 | `feat/tools2-write-edit-bash` |
| TOOLS3 + DF1-F2b (part) | Tool rows follow Pi's `ToolExecutionComponent` and per-tool renderers ([plan](specs/2026-09-30-tools3-tool-rows-plan.md)): one box per call (pending, then success/error background), read/grep/find/ls/write/edit/bash collapsed and expanded texts, compact `read` classification, edit's diff preview and word-level highlights (jsdiff `diffWords` ported), bash's last-5-lines preview, warning footer and `Took` (bash only), extension renderers and Pi's generic fallbacks. Tool results carry and store Pi's `details` (truncation, limits, edit diff, extension details; never sent to providers). Ctrl+O re-renders every row, extension rows included, live and restored, clearing the scrollback like Pi's full render; the render-details sink and the tools' side output are removed. Deviations: no syntax highlighting, links or ticking `Elapsed`; follow-on TOOLS3b | `feat/tools3-renderers` |
| DF1-F7 + DF1-F2b (TUI) | TUI polish ([plan](specs/2026-09-30-f7-tui-polish-plan.md)). `/compact` and automatic compaction redraw the chat like Pi's `compaction_end` (kept rows, then the `[compaction]` row; the prompt being sent is drawn after the kept rows), and summary rows are Pi's padded `customMessageBg` boxes. The `pi` theme is Pi's `dark` theme (truecolor and 256-colour codes from Pi's `theme.ts`), and the editor border follows the thinking level or `bashMode` (no ` ! bash ` label). A running `bash` row ticks `Elapsed Ns` every second. Status lines are Pi's dim `showStatus` rows without the `pipy  pipy:` prefix, with Pi's hotkey texts. Paints wait for startup (no stale editor frame), `quietStartup` hides the TUI header, and `--print`/`--mode json`/`--mode rpc` write no chrome to stderr. Deviations and the model-selection and compaction-prompt bullets: follow-on DF1-F7b | `feat/f7-tui-polish` |
| DF1-F7b (selectors) | Pi's `fuzzy.ts` port; `/model` exact-ref switch or fuzzy selector of available models (Tab scope toggle, Ctrl+S saves default); `/thinking` selector with search; slash menu fuzzy ranking and `/model`/`/thinking` argument completion; `/resume` search via Pi's `parseSearchQuery`; slash commands no longer drawn as user messages; `Error:` lines ([plan](specs/2026-09-30-f7b-selectors-plan.md)). Remainder in the F7b follow-on | `feat/f7b-selectors` |
| SYS1b | System messages reach providers ([plan](specs/2026-09-30-sys1b-provider-system-messages-plan.md)). Catalog compat flags from Pi's data (`supportsMidConvoSystemMessages`, `supportsAdditionalTools`, `supportsMidConvoToolChanges`, `supportsDeveloperRole`; `supportsMidConvoToolAdditions` via `models.json`); requests carry the session's anchored system messages for adapters that accept them: Responses/Azure/Codex developer messages with `additional_tools`/`tool_search` loads, Chat Completions/Mistral, Anthropic held `system` messages with `tool_addition`/`tool_removal`, placeholder and beta; Google/Bedrock collapse. Tagged prompt sections; `added_tool_names` and `supportsToolReferences` removed (Pi). Remainder: SYS1c | `feat/sys1b-provider-system-messages` |

## Follow-ons

Open deviations collected from the done slices, not yet queued. Take one when
a user need makes it matter. The DF1 items come first; their repro steps are in
[the DF1 acceptance note](acceptance/2026-09-29-df1-dogfooding.md).

- **READ-IMG, images from `read`:** Pi's `read` returns jpg/png/gif/webp/bmp
  files as image attachments (resized). pipy tool results are text-only, so
  `read` returns an error for images. Porting needs image content in tool
  results across the provider adapters, the session tree and replay.
- **TOOLS3b, what TOOLS3 left of tool rows** (see the TOOLS3 Done row):
  - no syntax highlighting in `read`/`write` rows (Pi uses highlight.js), no
    OSC 8 file links (DF1-F7 added the ticking `Elapsed`);
  - JSON/RPC `toolResult` messages and `tool_execution_end` carry no
    `details`; Pi's thrown tool errors store `details: {}`, pipy stores none;
  - `edit` stores no `details.patch` (jsdiff `createTwoFilesPatch`);
  - a hallucinated tool name gets the generic `name key=value` row where Pi,
    knowing no definition, prints the arguments as JSON;
  - the RPC `bash` command still runs through the allowlisted sandbox
    instead of Pi's executor.
- **DF1-F2b, restored-history rendering gaps** (left by F2/F3; TOOLS3 closed
  the extension-row expansion and tool-result details, F7 the live compaction
  redraw):
  - Reasoning text is not stored, so restored history has no thinking
    blocks (it touches the assistant message model and provider replay).
    Durations are not stored in Pi either: a restored `bash` row has no
    `Took` line in both.
  - A resumed session shows no `Session compacted N times` status (Pi
    `renderInitialMessages`).
- **DF1-F4b, what F4 left of retries:** F4 is merged (plan:
  `docs/parity-loop/plans/f4-retry-visibility.md`). Every provider is retried
  with Pi's classifier, also after partial output. The TUI shows `Error: ...`
  and `Retrying (n/3) in Ns... (escape to cancel)`. Codex `error` /
  `response.failed` events carry Pi's text and are classified by it. A failed
  turn keeps the last successful footer context. Remaining differences from
  Pi:
  - The retry stays inside one assistant message, so there is no
    `message_end` / `agent_end(willRetry)` for a retried attempt and it is not
    persisted. With F6, the turn stores one message: the success, one
    `error` message after the last failed attempt, or one `aborted` message
    when the wait is cancelled.
  - Escape during the backoff ends as an operator abort (an extra `Operation
    aborted` line).
  - There is no chars/4 trailing estimate on top of the last usage.
  - Compaction and branch-summary retries are still private.
- **DF1-F5b, summary input still too large:** tool-result truncation (F5)
  does not bound the whole summary request. A removed range with a huge pasted
  user message, large tool-call arguments or many results still fails the
  summary preflight on every later prompt, and only `/new` recovers. Pi has the
  same limit (its summary request fails at the provider). A fix beyond Pi would
  need a multi-pass or drop-without-summary path; Pi's token-based cut
  (`keepRecentTokens`, still inactive in pipy) would at least shrink the range.
- **DF1-F6b, what F6 left of stopped turns** (see the F6 Done row):
  - A live provider failure still prints
    `pipy: provider failure during turn: …`; Pi draws `Error: <message>`
    after the partial text, which pipy now shows only on resume.
  - Steering and local-command interruptions draw no live
    `Operation aborted` row (resume shows it for every aborted turn).
  - An abort during tool execution ends the run after the interrupted tool
    results; Pi's loop then records an empty aborted assistant.
  - Compaction and branch summaries drop a stopped turn's partial text; Pi
    serializes it into the summary input as `[Assistant]: …`.
- **DF1-F7b, what F7 left of TUI polish** (see the F7 Done row):
  - Selectors: the F7b selectors slice
    ([plan](specs/2026-09-30-f7b-selectors-plan.md), branch
    `feat/f7b-selectors`) ported Pi's `fuzzyFilter`, `/model <ref>` exact
    matching, the searchable model and thinking selectors, the fuzzy slash
    menu with `/model`/`/thinking` argument completion, the `/resume`
    search, no user message for commands, and `Error:` lines on those
    paths. It leaves:
    - `/login` argument completion (Pi's login provider options); extension
      commands have no `getArgumentCompletions`;
    - the model selector does not refresh catalogs (Pi's `Refreshing model
      catalogs…` / `Model catalogs refreshed.` line), and `/model <ref>`
      does not refresh before its second lookup;
    - the search input lacks Pi `Input`'s undo, yank, word motion and word
      deletion and forward Delete (pipy decodes no `Delete` key), and edits
      by code point, not grapheme cluster;
    - the `/resume` picker has no `relevance`/`threaded` sort and searches no
      message text (`allMessagesText`); `re:` uses Python's regex syntax;
    - a skill run draws its expanded text as a user message; Pi draws a
      `SkillInvocationMessageComponent`;
    - argument completion opens on any edit after `/<command> `; Pi's editor
      opens it only for letters, digits, `.`, `-` and `_`;
    - the plain REPL keeps pipy's `/model <ref>` resolver and its command
      bubbles; the settings dialog's provider/model list is still pipy's
      list; the default model is pipy's defaults store, not Pi's
      `defaultProvider`/`defaultModel` settings.
  - The compaction summary treats its own instruction as a user request:
    pipy sends the removed messages as structured history followed by the
    instruction. Pi serializes them into one `<conversation>` text
    (`serializeConversation`) with its structured `SUMMARIZATION_PROMPT` /
    `UPDATE_SUMMARIZATION_PROMPT`, split-turn prefix summaries and the
    read/modified file lists (a compaction request slice; see also F6b).
  - Notice texts stay pipy's; outside `/model` and `/thinking`, notices that
    Pi shows with `showWarning`/`showError` (`Warning: …` / `Error: …`) are
    still dim status lines, and back-to-back statuses are not merged into one
    line. An unhandled `/…` line gets pipy's notice; Pi sends it as a prompt.
  - The non-quiet TUI header lists every hint; Pi shows its logo and a
    condensed list that Ctrl+O expands.
  - `!` rows keep pipy's boxed style; Pi's `BashExecutionComponent` draws
    `bashMode` borders and a bold `$ command`.
  - The expanded compaction and branch summaries and the expanded `[skill]`
    box are not Markdown-rendered (Pi renders them with `Markdown`).
  - pipy compacts before sending a prompt, so an automatic `[compaction]` row
    comes before that prompt's answer; Pi compacts after the answer (or
    after an overflow error) and draws the row after it.
  - Pi draws its working loader inside the editor's top border
    (`── ⠏ Working ──`) while a tool runs; pipy shows no loader there.
  - Pi's default theme is `system` (colours generated from the terminal's);
    pipy's `pi` theme is Pi's `dark`, and other Pi theme tokens (Markdown,
    syntax, `border`/`borderAccent`, `selectedBg`) have no pipy field. The
    `pipy` title stays (bold `accent`) where Pi draws its logo.

- **Publishing:** choose and own a PyPI distribution name, then add a
  publish workflow with SHA-pinned actions. Until then
  `pipy update self` refuses, and the README documents the checkout install.
- **USAGE1b, what USAGE1 left of Pi's usage** (see the USAGE1 Done row):
  - the context meter and RPC `contextUsage` still read the live session's
    last response and estimate after a resume; Pi `getContextUsage` reads the
    last valid assistant usage on the branch (after the latest compaction);
  - compaction and branch summaries record no `usage` (Pi stores it on the
    entry and counts it); there are no `usage` entries (Pi cache warming);
  - assistant messages have no `api`, `responseModel`, `responseId` or
    `timestamp`; the `/session` breakdown keys on the requested model;
  - the streamed partial keeps `stopReason: "stop"` where Pi now sends
    `pending`, and has no `provider`/`model`;
  - `/session` has no `Cache Warming` section and no styling; resume and
    `/model` restore still need a `model_change` although assistant messages
    now name their model.
- **SYS1c, system-message remainder:** SYS1c
  ([plan](specs/2026-09-30-sys1c-system-message-remainder-plan.md), branch
  `feat/sys1c`) ported the leading prompt's shape (Responses/Azure first
  input item, Chat Completions instruction role, empty prompt omitted, Codex's
  empty-prompt default), sessions without a leading system message (made
  before SYS1a), `_restoreToolsFromTranscript` on `/tree` navigation (Pi's
  CLI never restores at startup: it always passes the configured tools) and
  Anthropic mid-conversation effort (`supportsMidConvoEffort`,
  `providerThinkingLevel`, `output_config` system messages, `block_binding`,
  the two betas). Still to port from Pi:
  - tool `strict` fields; Anthropic's other betas (fine-grained tool
    streaming, interleaved thinking, server-side fallback) and temperature;
  - OpenRouter Claude rows over the native Messages API (`4e69b0c28`), which
    also carry `supportsMidConvoEffort`;
  - `providerThinkingLevel` on streamed partials and on stopped (aborted or
    failed) assistant messages;
  - kept on purpose: Pi's Codex adapter on a model without
    `supportsMidConvoSystemMessages` sends only the initial system message
    as `instructions`, dropping later section updates; pipy sends the
    replayed prompt.
- **Thinking and model switches:** an extension `setThinkingLevel` does not
  rebuild the provider; `/model x:level` clamping; the 128k context fallback for
  a selection without a catalog row.
- **Prompt cache:** openai-completions `prompt_cache_key` and affinity
  headers; Codex WebSocket reuse and per-session SSE fallback; uuidv7 routing
  ids; Bedrock cache gate reads `model.name`;
  prompt-cache warming (`c596d09d9`).
- **Catalog and cost:** built-in rows carry no cost tiers (xAI and Copilot
  included); extension provider rows carry no cost; `models.json` rejects
  negative cost where Pi accepts it; Mistral rows are text-only and a reasoning
  Mistral row without a map sends the raw level; display names are not
  compared; legacy rows (Claude 3.5, gpt-5.1-codex, Gemini 2.x) can be pruned;
  OpenRouter Claude rows need Anthropic Messages over OpenRouter.
- **Providers:** xAI OAuth login; Copilot token persistence (Pi writes it back
  under a file lock) and `Retry-After` on 429.
- **OpenAI Sign in with ChatGPT (Pi `02eed88fd`, `72abf01ba`):** done
  2026-09-30 (`/login openai`, the global `deviceId`, persisted rotated
  refresh under the auth-file lock, the omitted cache fields, the usage-limit
  link and retry codes; plan `docs/plans/2026-09-30-openai-chatgpt-sign-in-plan.md`).
  Still open: pipy's line-based `/login` has no Pi auth-type selector
  ("Sign in with ChatGPT" / "Sign in with an API key") or API-key dialog, and
  Pi's `PI_OAUTH_CALLBACK_HOST` is honoured only by this flow, not by the
  Codex login.
- **Skills and prompt:** done 2026-09-30 on branch
  `feat/prompt-sections-skills`
  ([plan](specs/2026-09-30-prompt-sections-skills-plan.md)): Pi's default
  prompt with its `tools`/`rules`/`docs` sections (rebuilt for each run's
  active tools, pinned against Pi's output under Node),
  `disable-model-invocation`, the bash skill advertisement, `/skill:name`
  with Pi's skill block and its `[skill]` box, `.md` skill naming, Pi's
  package-first / CLI-last resource order and symlinked templates and
  commands. Left for later slices:
  - skill and prompt-template discovery still skips secret-shaped,
    generated and binary files and stops at pipy's 64 KiB per-file /
    256 KiB total caps; Pi's loaders have none of these screens;
  - Pi's bash guideline names `PI_*` session environment variables that
    pipy's bash tool does not set (pipy acts as Pi with
    `exposeSessionEnvironment: false`);
  - extension tools have no `promptSnippet`/`promptGuidelines`, and
    `before_agent_start` gets no `systemPromptOptions`;
  - Pi also refreshes the sections before each turn inside a run; pipy
    rebuilds them per run;
  - `/reload` rebuilds the `skills` section, but the `project_context` and
    `addendum` sections keep the session-start files (Pi's
    `_rebuildSystemPrompt` re-reads both);
  - package extensions and themes still load at the lowest precedence (Pi
    resolves package resources before the auto roots for every kind);
  - settings `skills`/`prompts` path entries; the wheel does not ship the
    README and docs the `docs` section names; skill descriptions are cut at
    256 characters and `<location>` is the resolved path.
- **Timing display:** response time and tok/s (tau `7b96883`) and tau's
  reactive model/thinking preview stay pipy product decisions; Pi has neither.
- **Repo hygiene:** done 2026-09-29. The three merged remote branches were
  deleted; the four stashes are kept as local `archive/stash-*` branches.

## Deferred and watch list

Revisit these when an active item needs them or the Pi surface stabilizes.

- **Pi MCP / codemode / tool search** (`8562bcf66`, 2026-09-29). This is L and
  still moving. Revisit in about two weeks.
- **Strict tool schemas** (Pi 0.86.0): only once a primary provider needs
  capability-gated constraints.
- **Per-model image input limits and resize** (Pi `f5c946480`).
- **Missing RPC commands:** `clear_queue` (`a79b37334`), prompt disposition
  (`e473b5cd8`), and `get_tree`/`get_entries` (`7ba1b6bfe`).
- **Extension events** that Pi's `core/extensions/types.ts` has and pipy's
  `extensions/activation.py` lacks.
- **Pi's shared OAuth callback server** (`4df157433`). This is a refactor only:
  pipy already has `AnthropicOAuthProvider` (`oauth_providers.py:117`).
- **Other deferred items:**
  - per-model compaction overrides (Pi `46bde88a1`)
  - live Codex model discovery
- **Standing deferrals:**
  - Parallel tools, until latency evidence starts with explicitly safe reads.
  - Image generation.
  - Distributed agents, durable lanes and server mode.
  - Full component/theme parity.
  - Measure startup/cancel latency and request size before setting performance
    gates.

## Minimum acceptance and constraints

Daily-use acceptance means:

- completing a real repository task and running its checks
- interrupting and steering work
- resuming after exit
- continuing after compaction without reconstructing the task by hand

A provider failure must leave a usable session, and an extension must be
addable through the documented APIs. Automated assertions cover lifecycle and
request content. Semantic summary quality and UX need live dogfooding. DF1
covered this on openai-codex; the other families still need it. Tests alone are
not a daily-usability claim.

The following stay as they are:

- the synchronous native core
- provider-neutral authority and event contracts
- full-content private product sessions
- the separate metadata-only learning archive
- project trust and extension publication boundaries

Preserve the existing behavioral and shared-state tests. Do not add a new async
runtime, DI framework, universal event bus, decomposition program or exact
file-size gate. Isolate tests from global settings, and leave both local theme
stores on `pi`.

## Contract map

Current behavior lives in these docs:

- [Architecture](architecture.md)
- [Harness Spec](harness-spec.md)
- [Session Tree](session-tree.md)
- [Compaction](compaction.md)
- [Python SDK](sdk.md)
- [Extension API](extension-api.md)
- [Provider Catalog](provider-catalog.md)
- [Settings & Config](settings-config.md)
- [Automation & RPC](automation-rpc.md)
- [TUI Workflow](tui-workflow.md)
- [Export](export-distribution.md)
- [Session Storage](session-storage.md)
- the [transactional reload contract](specs/2026-07-25-transactional-extension-reload-rebuild.md)

A dated filename does not make a runtime contract obsolete.

The following are evidence, not alternative queues. Their next-step
instructions do not override this index.

- the [July assessment](2026-07-29-architecture-quality-assessment.md)
- the [Pi gap audit](pi-mono-gap-audit.md), pinned to Pi 0.82.0
- the [historical parity inventory](parity-plan.md)

## History

- **D0–D8 (2026-09-07 → 2026-09-09).** The task table, per-slice narrative,
  investigation basis, thread protocol and review record are in
  [archive/backlog-2026-09.md](archive/backlog-2026-09.md). Its key
  milestones:

  | Milestone | Commit |
  | --- | --- |
  | D0 baseline | `f1fa668` |
  | D1 semantic compaction | `527b846` |
  | D2 product sessions | `faffcd0` |
  | D3 budgets and within-run compaction | `68dddcf` |
  | D4 recovery | `8c34a9c` |
  | D5 shared controls | `112397a` → `0bfd48c` |
  | D6 reopen/transitions/SDK retirement | `6e38076`, `b370a8a`, `b275c82` |
  | D7 RPC bash | `66189ed` |
  | D8 replay | `73f4aef` |
  | MC1–COST1, PR1/PR2, DH1 | see [Done](#done-2026-09-29) |

- **Older programs.** The architecture migration, quality, transactional
  reload, comparative remediation and god-file decomposition programs are
  complete. Earlier backlogs and plans live in Git: use
  `git log --all -- <path>` and `git show <commit>:<path>`.
