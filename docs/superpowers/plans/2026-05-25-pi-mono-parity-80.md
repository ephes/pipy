# Pi-Mono 80%-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to execute this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Per-feature plans live as separate documents under `docs/superpowers/plans/` — this is the master tracker.

**Goal:** Raise pipy's feature coverage from 23/50 (46%) to ≥40/50 (80%) of the locked pi-mono parity criterion in `docs/parity-criterion.md`, with at least 5 "big" features included, while preserving every pipy architectural invariant (stdlib-only deps, metadata-first archive, .git default-deny, no third-party schema validators).

**Architecture:** Each feature is one bounded slice that follows pipy's existing port/adapter patterns. Providers mirror `openai_provider.py` (urllib + stdlib JSON, `ProviderPort` protocol). Tools mirror `tools/write.py` (`ToolPort` + manual JSON-schema validation). Session features extend `HarnessRunner` and `pipy_session.recorder` without changing the metadata-first invariant.

**Tech Stack:** Python 3.13, stdlib only (urllib, json, dataclasses, asyncio for streaming), pytest, mypy, ruff. No new third-party runtime deps.

---

## Sequencing Strategy

17 features needed for 80%. Grouped into 5 waves so subagents can run in parallel within a wave without colliding on shared code (cli.py wiring is integrated by the lead agent between waves):

### Wave 1 — Provider parallelism (4 big features)
Parallel subagents, each touches a new provider file + its own test file only. Lead agent integrates CLI registration + docs after all four land.

- A5. **Anthropic provider** (big) — Messages API + tool calls
- A6. **Google Gemini provider** (big) — Generative AI API + tool calls
- A8. **Mistral provider** (big) — Mistral API + tool calls (OpenAI-compatible shape)
- A9. **Amazon Bedrock provider** (big) — SigV4 + InvokeModel for Claude-on-Bedrock

### Wave 2 — Tool parallelism (3 features, 1 big)
- B7. **bash tool** (big) — bounded subprocess execution boundary
- B8. **edit-diff helper tool** — unified-diff-driven edit
- B9. **truncate helper tool** — bounded output truncation utility for the loop

### Wave 3 — Reliability + streaming (2 big features)
- C14. **Streaming output** (big) — provider-side streaming for OpenAI + Anthropic, surfaced to stdout
- C15. **Retry/backoff** (big) — exponential backoff with jitter for 429/5xx

### Wave 4 — Additional providers (3 features)
- A4. **openai-completions provider** — Chat Completions API (distinct from Responses)
- A10. **Azure OpenAI Responses provider** — Azure endpoint shape
- A11. **Cloudflare provider** — Workers AI API

### Wave 5 — Resource loading + dynamic session (3 features, 1 big)
- D4. **Skills loading** — workspace skills directory discovery
- D5. **Prompt templates** — template file discovery + injection
- E5. **Dynamic provider/model swap** (big) — `/provider <name>` mid-session

### Wave 6 — Catch-up if needed (2 features)
- A7. **Google Vertex provider** — same shape as A6 with different auth
- E4. **Session export** — `pipy-session export <id>` to portable format

### Total: 17 features
With wave 1 providing 4 of the required 5 "big" features, wave 2's bash giving the 5th, and waves 3/5 adding 4 more big features for a margin of safety (9 big total).

## Per-Feature Acceptance Bar

Every feature lands as:

1. Module file at the path in `docs/parity-criterion.md`.
2. Hermetic tests covering happy path + at least two failure modes.
3. `just check` green.
4. README + `docs/pi-parity.md` updated to reflect new state.
5. Conventional commit landed.
6. The verify command in `docs/parity-criterion.md` returns the expected exit code.

## Verification Step

After each wave, run `just check`. After the final wave, dispatch an independent verification subagent that:

1. Re-reads `docs/parity-criterion.md`.
2. Runs each `Verify command` independently.
3. Counts ✅ rows.
4. Confirms ≥40 ✅ AND ≥5 "big" features pass.
5. Confirms `just check` is green.
6. Reports a final pass/fail.

Goal is met only when that independent subagent reports PASS.

## Per-Feature Sub-Plans

Each wave's features have their full plan written as a separate document under `docs/superpowers/plans/`, named `2026-05-25-feature-<feature-id>.md`. The dispatching agent reads the per-feature plan, executes its steps, and commits.

The per-feature plans use the bite-sized TDD step format from the writing-plans skill.
