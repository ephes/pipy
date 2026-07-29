# Pi-Mono Gap Audit

Status: product-gap selection snapshot refreshed 2026-07-29. It is a queue
input, not authorization to implement a gap inside the architecture closeout.

Reference: local `/Users/jochen/src/pi-mono` commit
`7df73a00c6cf85c000bf1ce1594c9284067a92f0`, package version `0.82.0`.
That commit is the empty post-release `[Unreleased]` opener immediately after
tag `v0.82.0` (`083e6162`). Reproduce the reference with:

```sh
git -C /Users/jochen/src/pi-mono rev-parse HEAD
node -p "require('/Users/jochen/src/pi-mono/packages/coding-agent/package.json').version"
```

This page is a selection aid, not an implementation plan and not a claim of
line-by-line TypeScript parity. Detailed pipy behavior remains owned by the
topic specs and conformance gates. The reviewed
[Architecture Quality Improvement Program](specs/2026-07-24-architecture-quality-improvement-plan.md)
is completed/reconciled historical evidence.

The final integration ledger is closed/reconciled at reviewed endpoint
`87c6f887f4afb719da89e68074551e9b8786ac1d`: 13 program/integration commits
since `fe474e0e55b3d1e8ae370534acb54a0a5fd9496b`, with 298 changed paths. The
exhaustive A-G partition union exactly covers all 298 changed paths:

- A: 29/29, 220,750 bytes/5,384 lines, valid complete CLEAN.
- B: 22/22, 359,459 bytes/8,776 lines, valid complete CLEAN.
- C: 14/14, 111,705 bytes/2,418 lines, valid complete CLEAN.
- D: 103/103, 410,314 bytes/9,494 lines, valid complete CLEAN.
- E: 150/150, 406,331 bytes/9,333 lines, valid complete CLEAN.
- Refreshed F: 19/19, 139,365 bytes/1,892 lines, valid complete CLEAN.
- G: 8/8, 36,717 bytes, valid complete CLEAN.

Slice 16 landed as `7deb8d8807f4e7eb52f7c9c8bd9e0ad30cb60727`
(`docs: close architecture quality program`). The three integration-fix commits
are the original Bundle F ledger fix
`ffeb86f0319efd28f6f360174ae640fa358761d0`
(`docs: reconcile architecture program ledger`), warning-state closure
`aea52b438713ce04fcad93ae32927ff156574aac`
(`docs: record integration warning closure`), and README/provider-catalog
closure `b64ceb7db9581bf3ebfab51f5803c513c1fdb549`
(`docs: align provider catalog status`). The prior valid complete exact-schema
cross-cutting review by Pi `openai-codex/gpt-5.6-sol` at committed endpoint
`b64ceb7` found the sole incomplete-ledger Warning: living ledgers omitted
refreshed F and `aea52b4`/`b64ceb7`. It found zero Critical or Suggestion
findings, omissions, forbidden tool uses, skips, truncations, or redactions.
Fresh exact-model Pi implementation fixed the ledger/test and metric
synchronization. A first focused review then found inaccurate
implementation/endpoint attribution and missing ratchets; those were corrected.
The final focused exact-schema G review covered 8/8 files and 36,717 bytes and
was valid CLEAN with no findings or coverage defects. That synchronization
landed as `87c6f887f4afb719da89e68074551e9b8786ac1d`
(`docs: sync final integration ledger`).

A fresh valid complete exact-schema cross-cutting re-review by Pi
`openai-codex/gpt-5.6-sol` at reviewed endpoint `87c6f88` covered A-G
manifests/reports, prior cross-cutting evidence, final ledger files, and
unchanged cross-contracts. The A-G manifest union exactly covers all 298 changed
paths. It returned `STATE: CLEAN`, `COVERAGE_COMPLETE: yes`,
`PARTITION_UNION_COMPLETE: yes`, and `VERDICT: CLEAN`, with zero Critical,
Warning, or Suggestion findings; `SCOPED_OMISSIONS: none`,
`FORBIDDEN_TOOL_USES: 0`, `SKIPPED_FILES: none`, `TRUNCATIONS: none`, and
`REDACTIONS: none`. Review stopped because the sole prior ledger Warning was
fixed and the fresh complete re-review was CLEAN; further review would add no
material value unless scope changes.

Latest stable verification for reviewed endpoint `87c6f88` is strict Mypy
across 169 source files, combined Mypy across 438 source/test files, and `just
check` at 4,829 passed / 2 skipped. Ruff formatting covers 480 files. Stable
metrics are 34 / 18 repository/source C901 findings, 81,738 / 121,191
source/test physical lines, 43 `ToolLoopTerminalUi` fields, one source ignore,
and 5,433 / 6,329 lines in `tool_loop_session.py` / `tui.py`. Docs are clean,
diff is clean, both theme sources are `pi`, and pre-commit is absent. Slice 14
stress evidence remains focused 20x, groups 10x, PTY smoke 5x, then the full
check; the latest PTY smoke is 8/8.

The architecture-quality program and final integration review are closed/reconciled. The explicit next
architecture boundary is bounded transactional-reload contract completion or
formal reconciliation before ordinary product-parity selection; none of the
product gaps below is part of Slice 16.

## Current reading

Pipy already has the important native product foundations: a headless canonical
agent loop, a headless coding-session layer, private full-content product
sessions, Pi-shaped JSON/RPC modes, a deep inline TUI, project trust,
catalog-driven construction for its implemented provider families, and a broad
Python extension host. The local Pi reference has moved from the previous
`b084d2fb` / 0.80.6 audit through releases 0.80.7–0.82.0. The material new
deltas are capability-gated constrained sampling, provider-owned OAuth and
catalog growth, session-aware/direct-RPC bash behavior, and broader retry
lifecycles. A current source comparison also confirms a pre-existing semantic
gap omitted from the earlier snapshot: Pi defaults model tool calls to parallel
execution with per-tool sequential overrides, while pipy executes them
sequentially.

Kimi deferred tools are **not “the next new Pi gap.”** Pi shipped the
Chat-Completions Kimi deferred-tool protocol in 0.80.8/0.80.9. Pipy's
provider-neutral load-point marker and Anthropic/OpenAI Responses projections
still do not implement that provider-specific shape, so it remains a valid
provider gap, but it is now an older candidate to rank alongside newer 0.81/0.82
deltas—not the active queue by default.

## Material delta classification

| Pi 0.81–0.82 delta | Evidence at `7df73a00` | Pipy status and selection guidance |
| --- | --- | --- |
| Constrained tool sampling and capability gates | `pi-ai` adds `Tool.constrainedSampling`: strict JSON Schema `prefer`/`require` plus OpenAI Lark/regex grammar tools. Current capability fields include `supportsOpenAIGrammarTools`, API-family `supportsStrictMode`, and Anthropic `supportsStrictTools`; they prevent unsupported request shapes across provider families. | **Missing, high-value but cross-cutting.** Pipy's tool definition and model spec have no constrained-sampling or matching capability fields. Specify the provider-neutral contract and fail-closed capability checks first; then land strict-schema and grammar wire families independently. Do not send unsupported constraints or silently degrade `require`. |
| Parallel tool execution | Pi defaults to parallel execution, allows per-tool `executionMode="sequential"`, emits finalized completion events in completion order, and emits the later tool-result message artifacts in assistant source order. | **Missing semantic contract.** Pipy's canonical `AgentLoop` iterates `assistant.tool_calls` sequentially and has no execution-mode vocabulary. Specify preparation, authority, cancellation, event/result ordering, and sequential overrides before implementation. |
| Kimi Code subscription OAuth | Pi's `kimi-coding` provider owns device authorization, refresh, host overrides, auth availability, and subscription model behavior. | **Missing provider/auth family.** Pipy has no Kimi Coding provider or provider-owned OAuth flow. Treat auth, provider transport/catalog rows, thinking/replay behavior, implied cost, and deferred tools as reviewed sub-slices rather than one “Kimi parity” bundle. |
| OpenRouter OAuth | Pi adds provider-owned PKCE login that mints a user-controlled API key usable by chat and image providers. | **Missing OAuth path; API-key provider already ships.** Pipy's OpenRouter Chat Completions adapter and API-key availability remain valid. Add PKCE only through the existing auth-store/catalog boundary, with secret-safe diagnostics and refresh/logout tests; do not fork a second OpenRouter construction path. |
| Session-aware bash environment | Built-in and factory-created Pi bash tools receive `PI_SESSION_ID`, `PI_SESSION_FILE`, `PI_PROVIDER`, `PI_MODEL`, and `PI_REASONING_LEVEL`; the tool explicitly clears inherited stale values before injecting the current session snapshot. | **Missing.** Pipy's model-visible bash streams output and enforces its existing process/timeout boundary, but does not inject equivalent run metadata. This is a bounded tool/composition slice, with privacy and stale-environment characterization required before implementation. Decide pipy-prefixed naming deliberately rather than copying Pi environment names accidentally. |
| Direct RPC bash updates and cancellation | Pi emits correlated `bash_execution_update` events for direct RPC `bash` and `abort_bash` cancels the live command through its abort controller. | **Missing protocol lifecycle.** Pipy's direct RPC bash runs on a bounded worker but emits no update event, and `abort_bash` explicitly returns an error while a command is running because that worker is not externally cancellable. Characterize correlation, update order, cancellation, terminal response, truncation, and stdout JSONL purity; updates and cancellation may land as separate reviewed cuts within one protocol family. |
| RPC thinking-level discovery | Pi 0.81 adds `get_available_thinking_levels` and a matching client method over the active catalog/model capabilities. | **Missing small RPC/catalog projection.** Pipy can set thinking level over RPC but does not expose this query. Add it only after pinning whether the response reflects the active model, scoped selection, and extension/catalog refresh snapshot. |
| Compaction and branch-summary retry lifecycle | Pi 0.81.1 routes transient summary failures through the configured retry policy and emits scheduled / attempt-start / finished events to interactive, JSON, RPC, and SDK consumers. | **Missing and semantically significant.** Pipy compaction/branch summarization does not expose this lifecycle. Add only after characterizing queue, cancellation, persistence, and true-idle behavior; these retries must not duplicate summary writes or consume queued input early. |
| Assistant/provider retry refinements | Pi classifies OpenAI Responses early EOF and DNS failures as retryable, makes OpenAI/Anthropic retry waits abortable and delay-bounded, and retries a missing Codex WebSocket continuation once without the stale continuation. | **Partially covered, not equivalent.** Pipy has robust pre-first-event Codex transport retry/fallback and cancellation, but the canonical agent policy remains zero-retry and other providers do not share Pi's lifecycle. Split transport classification, cancellable backoff, agent retry events, and Codex continuation recovery by owner. Preserve pipy's no-post-progress replay guarantee. |
| Provider/catalog growth and refresh | Pi adds Qwen Token Plan (international/China), a llama.cpp router plus Hugging Face search/load UI, complete native extension providers with refresh/filter/auth, generated catalog separation/freshness, provider-verified effort levels, and refreshed model/image rows. | **Real family of gaps, not one slice.** Pipy has catalog-driven construction, `models.json`, extension provider rows, and local ds4, but not these provider products or Pi's dynamic catalog machinery. Select one provider or catalog behavior at a time. Generated row churn alone is lower priority than capability/auth/transport correctness. |
| Usage/session refinements | Pi persists tool, compaction, and branch-summary usage in session totals and isolates summary requests with fresh routing session IDs and cache writes disabled. | **Partial.** Pipy tracks agent/provider usage and durable compaction metadata but should be compared with exact product-session totals and summary request/cache semantics before claiming equivalence. Keep accounting and routing/cache policy as separate slices if both differ. |
| Smaller 0.81–0.82 fixes | Literal bracketed scoped-model IDs, `/model` reloading changed `models.json`, model catalog startup refresh timing, Wayland clipboard fallback, loaded llama output limits, and release packaging/catalog freshness. | **Groom individually.** Some are applicable correctness fixes, some concern Pi-only packaging/provider surfaces, and none should be bundled into the architecture program. |

## Recommended queue after the architecture program

First complete or formally reconcile the bounded transactional reload contract
identified by the 2026-07-29 assessment. Only then select product work. The
ranking below is grooming input, not authorization to bypass the parity-loop
review protocol.

1. **Constrained sampling contract and capability gates.** It affects tool
   authority and provider request validity, so define the neutral model/tool
   vocabulary and unsupported-request behavior before adding wire variants.
2. **Parallel tool execution contract.** Define authority, preparation,
   cancellation, per-tool sequential overrides, completion-event order, and
   source-order result projection before adding a scheduler.
3. **Retry lifecycle decision.** Compare pipy's canonical zero-retry policy and
   Codex-only pre-progress retry with Pi's assistant, compaction, and branch
   summary lifecycles. Land one owner at a time, with JSON/RPC/SDK event and
   true-idle characterization.
4. **Direct RPC bash updates and cancellation.** The protocol family is useful
   once correlation, event ordering, cancellation, and truncation are pinned.
   Streaming updates and actual abort may remain independently reviewable cuts.
   Session-aware bash environment injection is adjacent but stays a separate
   tool-policy slice.
5. **Provider-owned auth:** OpenRouter PKCE first because the provider and
   API-key catalog path already exist; Kimi Code requires a larger new provider
   family and should follow a dedicated spec.
6. **Provider/catalog candidates:** Qwen Token Plan, llama.cpp router/model
   management, dynamic provider refresh, generated catalog freshness/effort
   metadata, and current row refreshes. Choose by user value and testability;
   never treat generated model count as parity by itself.
7. **Kimi Chat-Completions deferred tools.** Still a real missing adapter shape,
   but no longer a fresh “next gap”; schedule it with Kimi/OpenAI-compatible
   transport work when that provider family is selected.
8. **Small product polish and prior residuals:** bare-update realignment,
   Ctrl+X transcript copy, prompt-cache notices, automatic theme mode, output
   padding, and other previously audited request/auth details remain independent
   candidates.

## Shipped foundations not to reselect as large gaps

- native product session tree and the session/new/tree/resume/fork/clone/
  compact/import/export/share workflows;
- inline product TUI with editor history, paste, undo/redo, resize, selectors,
  overlays, queued steering/follow-up, images, folding, and cancellation;
- project trust, layered settings/keybindings, resource loading, managed-git
  packages, and local extension discovery/activation;
- canonical full-content events, Pi-shaped JSON/print/RPC modes, read-only
  session-tree RPC access, and true-idle `agent_settled`;
- extension commands, tools, providers, hooks, custom messages/entries,
  renderers, editor/components/overlays, chrome, terminal input, model controls,
  and session metadata actions;
- catalog-backed construction for pipy's implemented HTTP provider families;
- Anthropic and OpenAI/Codex Responses cache-friendly dynamic tool loading;
- GPT-5.6 Sol and `max` thinking; and
- OpenAI-Codex configurable timeout, pre-progress retries, cancellation, and
  WebSocket-to-SSE fallback.

Reusable TypeScript package API parity (`pi-ai`, `pi-agent`, `pi-tui`), the
experimental orchestrator, npm/PyPI execution, and literal source compatibility
remain outside the pipy-native product target unless product direction changes.

## Sources inspected

Pipy:

- `docs/parity-plan.md`, `docs/backlog.md`, `docs/pi-parity.md`, and the topic
  specs for providers, extensions, automation/RPC, settings, sessions, and TUI;
- `src/pipy_harness/native/agent/`, `coding/`, `automation/`, `providers/`,
  `provider_construction.py`, `catalog_data.py`, `auth_store.py`, `retry.py`,
  `openai_codex_provider.py`, `tools/bash.py`, `extension_runtime.py`, and
  `tool_loop_session.py`; and
- the current provider, automation/RPC, extension, session, and architecture
  conformance descriptions.

Pi at exact commit `7df73a00`:

- `packages/coding-agent/CHANGELOG.md` and `packages/ai/CHANGELOG.md` through
  0.82.0;
- coding-agent `core/agent-session.ts`, compaction and branch-summarization,
  `core/tools/bash.ts`, settings/model runtime, RPC types/server/client,
  interactive mode, and provider docs;
- pi-ai `types.ts`, `models.ts`, provider factories/catalogs, auth OAuth loaders
  (including Kimi and OpenRouter), constrained-sampling helpers, retry helpers,
  and OpenAI/Anthropic/Bedrock/Google/Mistral adapters; and
- git history from the prior broad reference `b084d2fb` through `7df73a00`,
  including the tagged 0.80.7–0.82.0 release changes.
