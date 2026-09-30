# USAGE1: usage stored per assistant message

Backlog: `docs/backlog.md` follow-on **USAGE1** (recorded by COST1 and SYS1a).
Reference: `~/src/pi-mono` at `1b347794e`.

## What Pi does

- `packages/ai/src/types.ts:427-448, 546-570`: every `AssistantMessage` has
  `provider`, `model` and `usage: Usage`:
  `{input, output, cacheRead, cacheWrite, cacheWrite1h?, reasoning?,
  totalTokens, cost: {input, output, cacheRead, cacheWrite, total}}`.
  `input` is the uncached prompt; `reasoning` is a subset of `output`, set only
  by providers that report it; `cacheWrite1h` is the 1h part of `cacheWrite`
  (Anthropic only). The adapter prices each response with
  `calculateCost(model, usage)` (`models.ts:1193-1213`), filling the four cost
  parts and their total.
- The message, usage included, is persisted as a `message` entry and emitted
  on `message_update` (partial), `message_end`, `turn_end`, `agent_end` and
  `get_messages`. Verified on the faux driver: the partial carries a `usage`
  object too.
- `core/usage-totals.ts`: `UsageTotals {input, output, cacheRead, cacheWrite,
  cost}`, `addUsageToTotals` (sums `cost.total`), `getUsageCostBreakdown`
  (groups assistant usage by `provider/(responseModel ?? model)`, tool/summary
  usage under `Tools/summaries`, drops empty groups, sorts by cost desc).
- `core/agent-session.ts:4080-4136` `getSessionStats()`: iterates
  `sessionManager.getEntries()` — **every entry in the file, all branches, and
  history compacted away** — counting message entries (`totalMessages`), user,
  assistant, toolResult, the toolCall blocks of assistants, and summing usage
  of assistant messages, toolResult messages with `usage`, `usage` entries and
  `compaction`/`branch_summary` entries with `usage`. Returns
  `{sessionFile, sessionId, userMessages, assistantMessages, toolCalls,
  toolResults, totalMessages, tokens: {input, output, cacheRead, cacheWrite,
  total = sum of the four}, cost, contextUsage?}`. RPC `get_session_stats`
  returns exactly this (`rpc-mode.ts:594`).
- Footer (`components/footer.ts:102-196`): the same all-entries sum;
  `latestCacheHitRate` = the last assistant entry's `cacheRead / (input +
  cacheRead + cacheWrite) * 100` (undefined when that prompt is 0). Parts are
  pushed individually when non-zero: `↑input` (uncached), `↓output`,
  `Rcache`, `Wcache`, then `CH<rate>%` only when the totals have cache
  activity and the rate is defined, then `$cost.toFixed(3)` (with ` (sub)`).
  Numbers use `formatTokens` (`footer.ts:25-31`: `<1000` as is, `<10000`
  `x.xk`, `<1M` `round(k)k`, `<10M` `x.xM`, else `round(M)M`).
  Because totals come from the stored entries they survive resume and a model
  switch, and reset only with a new session.
- `/session` (`interactive-mode.ts:6477-6556`): a `Session Info` block with
  `Name:` (when set), `File:` (or `In-memory`), `ID:`, then `Messages`
  (`Total`, `User`, `Assistant`, `Tools: N calls, M results`), `Tokens`
  (`Input:` = input+cacheRead+cacheWrite with `toLocaleString`; when the
  prompt is > 0 and there is cache activity, `  Cached: R (x.x%)` and
  `  Uncached: input+cacheWrite` plus ` (W written to cache)` when W > 0;
  `Output:`, `Total:`), a `Cache Warming` section, and — when `cost > 0` or
  cache waste was found — `Cost` with `Total: $x.xxx`, the per-model breakdown
  lines `  key: $x.xxx (formatTokens tokens)` unless there is exactly one
  entry naming the selected model, and `Cache Re-billed:` from
  `computeCacheWaste` (`core/cache-stats.ts`).

## What pipy does today

The agent loop absorbs each `ProviderResult.usage` into a per-run and a
per-session `AgentUsageAccumulator`; nothing is stored on the message. The
footer and RPC `get_session_stats` read the session accumulator, which a model
bind replaces and which starts at zero on resume. `get_session_stats` counts
the active context messages. `/session` prints a one-line pipy-only status
(`pipy native session: name=… messages=… branches=…`). The footer's `↑` is
the raw input sum (inclusive of cache reads for OpenAI-style providers), `CH`
the cumulative hit rate, and `↑`/`↓` always appear together.

## Design

1. **Message model** (`native/agent/messages.py`): add frozen
   `AgentUsageCost(input, output, cache_read, cache_write, total)` (finite,
   non-negative floats) and `AgentMessageUsage(input, output, cache_read,
   cache_write, total_tokens, cost, reasoning: int | None = None,
   cache_write_1h: int | None = None)` (non-negative ints). Add three optional
   fields to `AgentAssistantMessage`: `usage: AgentMessageUsage | None`,
   `provider: str | None`, `model: str | None`, all default `None` and
   declared `compare=False`: they are telemetry about the turn, not
   conversation content, so equality of history (tests, retained-history
   checks) keeps its meaning. `coding/state.py` exact validation and
   `loop._validate_message` check the types.
2. **Per-message usage** (`native/agent/usage.py`): split COST1's `_turn_cost`
   into `turn_cost_parts(...) -> AgentUsageCost` (same Pi `calculateCost`
   arithmetic, tier from the whole prompt, 1h writes at 2x input) and add
   `message_usage(sample, pricing) -> AgentMessageUsage`, using the same
   uncached-input rule the accumulator already applies
   (`_cache_counters_are_separate`). `total_tokens` is the provider total when
   positive, else `input + output + cacheRead + cacheWrite` (Pi adapters'
   fallback). `reasoning`/`cache_write_1h` are set only when the provider
   mapping carries `reasoning_tokens`/`cache_write_1h_tokens`
   (`AgentProviderUsageSample` gains `reports_reasoning`/`reports_cache_write_1h`
   presence flags). The accumulator's `absorb` uses `turn_cost_parts(...).total`,
   so accumulated cost is unchanged.
3. **Agent loop** (`agent/loop.py`): `_publish_usage` returns the sample; the
   loop builds `message_usage(sample, run_input.pricing)` and attaches it,
   with `snapshot.request.provider_name`/`model_id`, to the completed
   assistant and to a failed turn's `error` message. An aborted turn (no
   result) gets zero usage and the request's provider/model when a snapshot
   exists (Pi's aborted message carries the usage streamed so far, which is
   zero for pipy's non-streamed usage).
4. **Persistence** (`session_tree.py`): `_message_to_json` writes `usage` in
   Pi's shape (`{input, output, cacheRead, cacheWrite, [cacheWrite1h],
   [reasoning], totalTokens, cost{…}}`) plus `provider`/`model`, each only when
   set; `_message_from_json` reads them back, rejecting malformed values like
   other fields. Old files load unchanged (`None`). `session.py` is not
   touched.
5. **Totals and stats** (new `native/session_usage.py`, pure functions over
   `NativeSessionTree.get_entries()`):
   - `usage_totals(entries)` → `SessionUsageTotals(input, output, cache_read,
     cache_write, cost, latest_cache_hit_rate)` over every assistant message
     entry (Pi `addUsageToTotals`; the footer's `latestCacheHitRate`).
   - `session_stats(tree)` → Pi `SessionStats` counts over every message
     entry (all branches), with tokens/cost from `usage_totals`.
   - `usage_cost_breakdown(entries)` (Pi `getUsageCostBreakdown`; key
     `provider/model`; a message without attribution is skipped — the loop
     sets usage and attribution together, so only zero-usage messages, which
     Pi drops as empty groups anyway, lack it).
   - `cache_waste(entries, cache_read_rate)` (Pi `computeCacheWaste`: reset
     on `compaction`/`branch_summary` entries, 1024-token noise floor,
     reported-cache stickiness, paid-vs-read per-token rate; the read-rate
     fallback is the row's `cacheRead` via `pricing_for`).
   - `format_tokens` (Pi `formatTokens`, `Math.round` half-up) and a `toFixed`
     helper: `Decimal(value)` built from the float itself (its exact binary
     value, never `str(value)`), quantized `ROUND_HALF_UP`. JS `toFixed`
     rounds the exact binary value, so `(0.0045).toFixed(3)` is `0.004`
     (0.0045 is stored just below the tie); a test pins that boundary and an
     exact binary tie (`0.0625` → `0.063`).
   - `format_session_info(tree, *, selected, cache_read_rate)` renders Pi's
     `/session` block (see deviations for Cache Warming).
6. **Footer** (`chrome.py`): `_ChromeFooterEffects` gets a
   `session_tree: Callable[[], NativeSessionTree] | None`, bound in
   `repl/wiring.py` as `lambda: ctl.session_tree` so `/new`, `/resume`,
   `/fork` and tree replacement are followed (the same live-pointer pattern
   wiring already uses for other consumers); the token/cost
   parts come from `usage_totals(tree.get_entries())` (the tree API is
   guarded, `get_entries` returns a copy). Pi caches this sum because its
   footer renders every frame; pipy composes the footer text on events (turn
   start, input read, model changes), so it sums without a cache.
   `format_bottom_status_line` pushes
   `↑`/`↓`/`R`/`W` individually when non-zero with `format_tokens`; `CH` uses
   the latest message's rate only when the totals have cache activity. The
   context meter still uses the live accumulator's `last_total_tokens`
   (unchanged; see deviations).
7. **RPC** (`automation/rpc.py`): `get_session_stats` returns
   `session_stats(self._tree)` — all stored entries, Pi's counters and totals.
   `CodingSessionUsageSnapshot.uncached_input_tokens` and
   `cache_hit_percent`, whose only consumers were the RPC stats and the
   footer, are removed with the accumulator fields that only fed them
   (no-deprecation policy) if nothing else reads them.
8. **JSON** (`automation/serialize.py`, `automation/agent_events.py`):
   assistant messages always carry `usage` in Pi's shape (zeros when the
   message has none, e.g. from an old session) and `provider`/`model` when
   known. The streamed `message_update` partial carries a zero `usage`.
   `automation_pi_comparison.py` gains a check that the assistant `usage` key
   set (and `cost` key set) matches Pi's faux-driver message.
9. **`/session`** (`repl/session_commands.py`): `SHOW_SESSION_STATUS` prints
   `format_session_info(...)` instead of the pipy-only one-liner.
   `SessionCommandEffects` gains two bindings, wired in `repl/wiring.py`:
   `selected_model: Callable[[], tuple[str, str]]` (the coding state's live
   `provider_name`/`model_id`, Pi `session.model`) and
   `cache_read_rate: Callable[[str, str], float]` (the row's `cacheRead`
   $/M via `turn_leaves.pricing_for(provider_state, …)`, 0 without a row).
   The tree is read through the existing `self.ctl.session_tree`.
   `session_tree_commands.format_session_status` and its only helper
   `_branch_count` are removed.

## Deviations (documented)

- No `Cache Warming` section: pipy has no cache warming (`c596d09d9`, backlog
  Prompt cache follow-on).
- Usage from compaction and branch summaries, tool results and `usage`
  entries is not recorded (pipy has no such usage today); totals are
  assistant-only.
- No `responseModel`, `api`, `timestamp` or `responseId` on messages; the
  breakdown key uses the requested model. Messages from old sessions have no
  usage and no attribution; they count as zero.
- The streamed `message_update` partial keeps `stopReason: "stop"` (Pi now
  sends `pending`) and has no `provider`/`model`; a separate small gap.
- The context meter (`getContextUsage`) and RPC `contextUsage` stay as before:
  the live accumulator's last total, estimated on resume. Named follow-on.
- `/session` is plain text in the transcript (no bold/dim styling).
- The one-shot `session.py` runtime stores no usage (line ceiling; not a
  product path).
- F4 retried attempts are not stored as messages (DF1-F4), so their usage is
  not in the totals.

## Done when

- Unit tests: `message_usage` (uncached split for inclusive and separate cache
  counters, tiers, 1h writes, optional fields), loop attaches usage and
  provider/model to completed and failed messages and zero usage to aborted,
  tree round-trip plus an old entry without usage, `usage_totals`/
  `session_stats` over two branches and a model switch, breakdown, cache
  waste, `format_tokens`, `format_session_info`, footer line semantics, RPC
  `get_session_stats` after resume counts stored entries, JSON message and
  partial shapes.
- PTY with the local completions stub reporting usage and a catalog cost:
  footer cost after two turns, `/session` Cost section, exit, `pipy -r`,
  same footer total and `/session` totals, `/model` switch keeps totals.
- Live openai-codex `gpt-6.1-sol` two-turn run plus resume in an isolated
  config (if credentials are available and cheap).
- `just check`, every `scripts/parity_checks/` script and
  `scripts/parity_score.sh` pass; docs (`automation-rpc.md`,
  `session-tree.md`, `tui-workflow.md`), CHANGELOG `[Unreleased]` and the
  backlog USAGE1 entry updated.
