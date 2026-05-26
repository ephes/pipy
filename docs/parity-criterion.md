# Pi-Mono Parity Criterion (Locked 2026-05-25)

This document **locks in the hard, objective criterion** used to measure pipy's
feature parity against `~/src/pi-mono` for the 80%-parity goal. It is
deliberately reproducible: any reviewer or independent agent can re-run the
verification commands and arrive at the same score.

The denominator is grounded in pi-mono's actual on-disk structure (its provider
files, tool files, and documented subsystems) — not subjective rubrics.

## Locked Feature List (50 features)

For each feature: `STATUS` is one of `✅ done`, `🟡 partial`, `❌ missing`.
`VERIFY` is the shell command(s) that decide the status; a feature is `✅` only
when the verification commands pass against `pipy` HEAD.

### A. Providers (11 features)

Source of truth: every `.ts` file under `~/src/pi-mono/packages/ai/src/providers/`
that defines a provider class (excluding shared helpers `*-shared.ts`,
`*-headers.ts`, `register-builtins.ts`, `simple-options.ts`, `transform-messages.ts`,
`openai-prompt-cache.ts`).

| # | Feature | pipy status (2026-05-25) | Verify command |
| - | ------- | ------------------------ | -------------- |
| A1 | faux/fake provider | ✅ | `test -f src/pipy_harness/native/fake.py` |
| A2 | openai-responses | ✅ | `test -f src/pipy_harness/native/openai_provider.py` |
| A3 | openai-codex-responses | ✅ | `test -f src/pipy_harness/native/openai_codex_provider.py` |
| A4 | openai-completions (chat) | ✅ | `test -f src/pipy_harness/native/openai_completions_provider.py` |
| A5 | anthropic | ✅ | `test -f src/pipy_harness/native/anthropic_provider.py` |
| A6 | google (Gemini Generative AI) | ✅ | `test -f src/pipy_harness/native/google_provider.py` |
| A7 | google-vertex | ✅ | `test -f src/pipy_harness/native/google_vertex_provider.py` |
| A8 | mistral | ✅ | `test -f src/pipy_harness/native/mistral_provider.py` |
| A9 | amazon-bedrock | ✅ | `test -f src/pipy_harness/native/bedrock_provider.py` |
| A10 | azure-openai-responses | ✅ | `test -f src/pipy_harness/native/azure_openai_provider.py` |
| A11 | cloudflare | ✅ | `test -f src/pipy_harness/native/cloudflare_provider.py` |

Additionally, pipy ships `openrouter` (not in pi-mono); it is counted as
**bonus** and does not affect the denominator.

**Per-provider acceptance:** the file must export a class that implements
`ProviderPort`, supports tool calling for providers that natively offer it,
includes hermetic unit tests against a stub HTTP transport, and is wired into
`pipy run --native-provider <name>` in `cli.py`.

### B. Tools (9 features)

Source of truth: every `.ts` file under
`~/src/pi-mono/packages/coding-agent/src/core/tools/` that defines a tool
(excluding shared helpers: `file-mutation-queue.ts`, `output-accumulator.ts`,
`path-utils.ts`, `render-utils.ts`, `tool-definition-wrapper.ts`, `index.ts`).

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| B1 | read | ✅ | `test -f src/pipy_harness/native/tools/read.py` |
| B2 | ls | ✅ | `test -f src/pipy_harness/native/tools/ls.py` |
| B3 | grep | ✅ | `test -f src/pipy_harness/native/tools/grep.py` |
| B4 | find | ✅ | `test -f src/pipy_harness/native/tools/find.py` |
| B5 | write | ✅ | `test -f src/pipy_harness/native/tools/write.py` |
| B6 | edit | ✅ | `test -f src/pipy_harness/native/tools/edit.py` |
| B7 | bash | ✅ | `test -f src/pipy_harness/native/tools/bash.py` |
| B8 | edit-diff | ✅ | `test -f src/pipy_harness/native/tools/edit_diff.py` |
| B9 | truncate | ✅ | `test -f src/pipy_harness/native/tools/truncate.py` |

**Per-tool acceptance:** module exposes a class implementing `ToolPort` and
gets registered in `production_tool_registry` (where appropriate for the model
loop). Hermetic tests covering happy-path + at least two failure cases.

### C. Core subsystems (15 features)

Source of truth: pi-mono's documented capabilities in its own README plus the
`packages/agent/src/` and `packages/coding-agent/src/core/` layouts.

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| C1 | CLI entry point | ✅ | `uv run pipy --help \| grep -q 'pipy'` |
| C2 | One-shot `run` mode | ✅ | `uv run pipy run --help \| grep -q 'goal'` |
| C3 | Interactive REPL mode | ✅ | `uv run pipy repl --help \| grep -q 'repl'` |
| C4 | Session persistence | ✅ | `uv run pipy-session list --help \| grep -q list` |
| C5 | Session catalog (list/search/inspect) | ✅ | `uv run pipy-session list && uv run pipy-session search --help` |
| C6 | Provider port abstraction | ✅ | `test -f src/pipy_harness/native/provider.py` |
| C7 | Tool port + registry | ✅ | `grep -q 'production_tool_registry' src/pipy_harness/native/tool_loop_session.py` |
| C8 | Workspace context (AGENTS.md/CLAUDE.md) | ✅ | `test -f src/pipy_harness/native/workspace_context.py` |
| C9 | System prompt composition | ✅ | `grep -q 'system_prompt' src/pipy_harness/native/workspace_context.py` |
| C10 | Tool budget + malformed recovery | ✅ | `grep -q 'tool_budget' src/pipy_harness/native/tool_loop_session.py` |
| C11 | .git default-deny + symlink resolution | ✅ | `grep -q '_resolved_relative_label' src/pipy_harness/native/read_only_tool.py` |
| C12 | Transcript sidecar (opt-in) | ✅ | `test -f src/pipy_harness/native/transcripts.py` |
| C13 | JSON output mode | ✅ | `uv run pipy run --help \| grep -q 'native-output'` |
| C14 | Streaming output (provider→stdout) | ❌ | `grep -rq 'streaming' src/pipy_harness/native/ \| head -1; grep -rq 'stream' src/pipy_harness/native/openai_provider.py` |
| C15 | Retry/backoff for transient provider errors | ✅ | `test -f src/pipy_harness/native/retry.py \|\| grep -rq 'RetryPolicy' src/pipy_harness/native/` |

### D. Workspace-context & resource loading (8 features)

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| D1 | Parent-walk for instruction files | ✅ | `grep -q 'parent' src/pipy_harness/native/workspace_context.py` |
| D2 | Per-file + total byte caps | ✅ | `grep -q 'byte_cap' src/pipy_harness/native/workspace_context.py` |
| D3 | Global config root (PIPY_CONFIG_HOME) | ✅ | `grep -q 'PIPY_CONFIG_HOME' src/pipy_harness/native/workspace_context.py` |
| D4 | Skills loading (workspace skills) | ✅ | `test -f src/pipy_harness/native/skills.py` |
| D5 | Prompt templates | ✅ | `test -f src/pipy_harness/native/prompt_templates.py` |
| D6 | Custom slash commands (user-defined) | ✅ | `grep -rq 'custom_commands\|user_commands' src/pipy_harness/native/` |
| D7 | Themes / color schemes | ✅ | `test -f src/pipy_harness/native/themes.py` |
| D8 | Image/binary attachment loading | ❌ | `grep -rq 'image_attachment\|load_image' src/pipy_harness/native/` |

### E. Advanced session features (7 features)

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| E1 | Session resume (replay from record) | ✅ | `test -f src/pipy_harness/native/session_resume.py \|\| grep -rq 'def resume' src/pipy_harness/native/` |
| E2 | Session compaction (LLM-summarize transcript) | ❌ | `test -f src/pipy_harness/native/session_compaction.py \|\| grep -rq 'compact' src/pipy_harness/native/` |
| E3 | Session branching/forking | ❌ | `grep -rq 'branch\|fork' src/pipy_harness/native/session.py` |
| E4 | Session export/share | ✅ | `uv run pipy-session export --help 2>/dev/null \|\| grep -rq 'def export' src/pipy_session/` |
| E5 | Dynamic provider/model swap mid-session | ✅ | `grep -rq 'def set_provider\|swap_provider' src/pipy_harness/native/` |
| E6 | Settings/config panel | ✅ | `grep -rq '/settings' src/pipy_harness/native/session.py` |
| E7 | RPC mode / SDK embedding | ❌ | `test -f src/pipy_harness/rpc.py \|\| test -f src/pipy_harness/sdk.py` |

## Scoring

```
✅ count / 50 = parity %

current ✅ count (2026-05-26, after 90% increment): 45
target  ✅ count for 80% parity:                    40
delta beyond 80% target:                            +5
```

## How To Verify

Run this script after each work session to recompute the score:

```sh
just parity-score
```

The `just parity-score` recipe re-runs the per-row `Verify` commands and
counts how many succeed. The pass bar is **40 ✅ out of 50** with the
constraint that **at least 5 of the implementations must be
"big" features** (one of: bash tool, anthropic provider, google provider,
streaming output, session resume, session compaction, retry/backoff with real
HTTP error injection tests, mistral provider, bedrock provider, dynamic
provider swap). This anti-gaming rule prevents reaching 80% by only adding
trivial helpers.

## What Counts As "Big"

For the anti-gaming bar:

- **bash tool (B7)**: real subprocess execution with timeout, output capture,
  archive-safe metadata, workspace-relative cwd. Not a stub.
- **anthropic provider (A5)**: real Anthropic Messages API call wiring with
  tool-call support, hermetic stub-transport test that round-trips at least
  one tool call.
- **google provider (A6)**: real Gemini Generative AI call wiring with
  tool-call support and hermetic stub-transport test.
- **mistral provider (A8)**: real Mistral API wiring, stub-transport test.
- **amazon-bedrock provider (A9)**: AWS SigV4-signed Bedrock InvokeModel call
  wiring (Claude on Bedrock minimum), stub-transport test.
- **streaming output (C14)**: provider streams chunks to a configurable sink
  during `pipy run`, with a hermetic streaming-stub test.
- **retry/backoff (C15)**: exponential backoff with jitter for 429/5xx, capped
  attempts, hermetic test that injects two failures then a success.
- **session resume (E1)**: a metadata-only reader resolves finalized archive
  records into safe continuation context, exposes it through
  `pipy-session resume-info <session-id>`, and has focused tests. Live native
  prompt seeding remains a follow-up.
- **session compaction (E2)**: a documented compaction pass that summarizes
  an oversized message envelope back into bounded form, with a test.
- **dynamic provider swap (E5)**: helper functions switch provider/model
  selection through `NativeReplProviderState.select_model`, preserving existing
  availability gates and non-secret default persistence, with focused tests.
  The `/provider` dispatcher hook remains a follow-up.

A "big" feature can ONLY count toward the anti-gaming bar after both:
(a) its Verify command passes, and (b) `just check` is green with its tests.

## What Is Explicitly Out Of Scope For The 80% Target

These pi-mono features are **excluded from the denominator** because they
depend on prerequisites pipy has deliberately not built (and the user did not
ask us to rebuild the prerequisite):

- TUI keybindings, kill-ring, undo-stack, terminal-image (require full TUI
  rewrite — pipy is line-oriented by design)
- Extension package loading from npm-style registries (pipy is Python)
- TypeScript SDK (pipy is Python)
- Browser smoke tests (no browser surface in pipy)

If the user later requests these, they'd be added back and the denominator
would grow.

## Architectural Constraints Retained

These pipy invariants are **NOT relaxed** by the parity push:

- **No new runtime dependencies.** All new providers must use `urllib` +
  stdlib JSON, mirroring `openai_provider.py`. No `httpx`, `boto3`,
  `anthropic`, `google-generativeai`, etc.
- **Metadata-first archive.** No new feature may write raw prompts, model
  text, tool payloads, file contents, diffs, or auth material to the
  pipy session archive.
- **`.git` default-deny** stays enforced for every new tool.
- **Symlink resolution** stays enforced.
- **No new third-party schema validators.** Use `validate_arguments` from
  `tools/base.py`.

A "big" feature that achieves its Verify by violating any of these
constraints does NOT count.
