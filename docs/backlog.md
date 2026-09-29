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

## Follow-ons

Open deviations collected from the done slices, not yet queued. Take one when
a user need makes it matter. The DF1 items come first; their repro steps are in
[the DF1 acceptance note](acceptance/2026-09-29-df1-dogfooding.md).

- **READ2, read path policy:** `read` still resolves paths
  through pipy's shared `resolve_tool_path`: workspace plus `--read-root`
  roots, with `.git` and `.gitignore` paths refused, and so are generated
  directories (`node_modules`, `build`, `dist`, `.venv`, `.pipy`, …) and
  suffixes (`.lock`, `.map`, `.min.js`, `.d.ts`, images, archives). Pi's `resolveReadPath`
  (`core/tools/path-utils.ts`) reads any path, expands `~`, strips a leading
  `@`, normalizes unicode spaces and tries the macOS screenshot, NFD and
  curly-quote variants. No security boundary needs the restriction (`bash`
  reads anything). The same resolver serves `ls`/`grep`/`find`, `@file` and
  `@image:`, so the slice decides each consumer.
- **READ-IMG, images from `read`:** Pi's `read` returns jpg/png/gif/webp/bmp
  files as image attachments (resized). pipy tool results are text-only, so
  `read` returns an error for images. Porting needs image content in tool
  results across the provider adapters, the session tree and replay.
- **TOOLS2, what TOOLS1 left of the tool output:** `bash` still frames its
  result as `exit code: N` + `[output]` and treats a non-zero exit as a normal
  result; Pi appends `Command exited with code N` and marks it an error, with
  `Command timed out after N seconds` / `Command aborted`. The `!`/`!!`
  shortcut keeps a 16 KB tail where Pi's `bash-executor.ts` uses
  `truncateTail` plus a temp file. Tool-row headers keep `grep "p" path`
  where Pi shows `/p/ in path (glob) limit N`. `ls` sorts by code point where
  Pi uses `localeCompare`. The bash temp file sits outside `read`'s path
  policy until READ2.
- **DF1-F2b, restored-history rendering gaps** (left by F2/F3, see the Done
  table):
  - Rows drawn by an extension tool's `render_call`/`render_result` keep the
    expansion state of the moment they were rendered, live and restored
    alike. Pi calls `setExpanded` on every `ToolExecutionComponent`.
    Re-rendering needs the renderer inputs retained (args, per-call state,
    result text, details).
  - A live `/compact` or automatic compaction shows its notice; Pi redraws the
    chat, so the `[compaction]` row appears at once. pipy shows that row only
    after the next redraw (resume, tree navigation).
  - Reasoning text, tool-result details and durations are not stored, so
    restored history has no thinking blocks, extension result details or
    `Took Ns`.
- **DF1-F4, retry UX:** fixed on `fix/f4-retry-visibility` (plan:
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
- **DF1-F7, TUI polish:**
  - Notices print `pipy  pipy: …`.
  - Non-bash tool rows keep the `$ ` prompt. Pi shows `edit path`,
    `find X in Y` and `grep /X/ in Y`.
  - `/model gpt-6-luna` resolves to the unauthenticated `openai` row.
  - The `/model` selector lists unavailable models; Pi lists only models with
    configured auth.
  - Startup leaves a stale editor frame in scrollback.
  - `-p` prints the startup chrome to stderr.
  - The manual compaction summary treated its own instruction as a user
    request.

- **Publishing:** choose and own a PyPI distribution name, then add a
  publish workflow with SHA-pinned actions. Until then
  `pipy update self` refuses, and the README documents the checkout install.
- **USAGE1:** per-message usage in JSON/RPC messages and the session tree,
  resumed-session totals, Pi's `/session` Cost section, totals across a model
  switch, and Pi's footer token semantics (uncached `↑`, latest-message `CH`).
- **SYS1b, system messages to providers:** SYS1a (see Done) records Pi's
  system messages in the transcript but still sends every model the prompt
  out of band (Pi's collapse path). Still to port from Pi `9e05370b2`:
  - the compat flags `supportsMidConvoSystemMessages`,
    `supportsMidConvoToolAdditions` and `supportsMidConvoToolChanges`, and
    per-adapter serialization of later system messages (Anthropic
    `tool_addition`/`tool_removal`, OpenAI Responses/Codex developer messages
    and `additional_tools`, Chat Completions, Google, Bedrock, Mistral);
  - Anthropic mid-conversation effort (`supportsMidConvoEffort`,
    `output_config` system messages, `4e69b0c28`);
  - replacing `AgentToolResultMessage.added_tool_names` deferred-tool loads
    with transcript `toolsAdded` (Pi removed `addedToolNames`);
  - restoring the active tool set from the transcript on resume
    (`_restoreToolsFromTranscript`);
  - Pi's tagged prompt sections (`tools`/`rules`/`docs`/`addendum`/
    `project_context`/`skills`/`cwd`) in place of pipy's single `preamble`;
  - `get_session_stats` counting stored message entries as Pi does (all pipy
    counters count the active context; see USAGE1).
- **Thinking and model switches:** an extension `setThinkingLevel` does not
  rebuild the provider; `/model x:level` clamping; no fuzzy
  search in selectors or `/thinking` completion; the 128k context fallback for
  a selection without a catalog row.
- **Prompt cache:** openai-completions `prompt_cache_key` and affinity
  headers; Codex WebSocket reuse and per-session SSE fallback; uuidv7 routing
  ids; Pi's deferred-tool placeholder; Bedrock cache gate reads `model.name`;
  prompt-cache warming (`c596d09d9`).
- **Catalog and cost:** built-in rows carry no cost tiers (xAI and Copilot
  included); extension provider rows carry no cost; `models.json` rejects
  negative cost where Pi accepts it; Mistral rows are text-only and a reasoning
  Mistral row without a map sends the raw level; display names are not
  compared; legacy rows (Claude 3.5, gpt-5.1-codex, Gemini 2.x) can be pruned;
  OpenRouter Claude rows need Anthropic Messages over OpenRouter.
- **Providers:** xAI OAuth login; Copilot token persistence (Pi writes it back
  under a file lock) and `Retry-After` on 429.
- **Skills and prompt:** template and command stores keep the symlink guard; a
  plain `.md` skill keeps its file stem; package skills load after the auto
  roots; not ported: `disable-model-invocation`, the bash-only skill
  advertisement and Pi's `<tools>`/`<rules>`/`<cwd>` prompt sections.
- **Timing display:** response time and tok/s (tau `7b96883`) and tau's
  reactive model/thinking preview stay pipy product decisions; Pi has neither.
- **Repo hygiene (owner):** the fully merged remote branches
  `feat/catalog-non-completions`, `feat/extension-api` and
  `feat/tui-interaction-comfort` can be deleted; four local stashes from
  2026-07-01 to 2026-08-02 wait for an owner decision.

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
