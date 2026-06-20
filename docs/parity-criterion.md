# Pi-Mono Parity Criterion

Status: the original 2026-05-25 80%-parity gate is complete and is now a
legacy baseline. Future parity planning should use the post-baseline product
surface matrix below plus the parity roadmap in `docs/parity-plan.md` and the
detailed specs in `docs/backlog.md`, `docs/pi-parity.md`, `docs/session-tree.md`,
`docs/extension-api.md`, `docs/provider-catalog.md`, `docs/settings-config.md`,
`docs/automation-rpc.md`, `docs/tui-workflow.md`, and
`docs/export-distribution.md`.

This document keeps two scores distinct:

1. **Legacy objective score** — the locked 50-row, script-verifiable baseline
   used to prove that pipy had crossed the original 80% target.
2. **Current product-surface stage** — a coarser map of the remaining Pi-class
   product surfaces after that baseline is complete.

## Current Stage (2026-06-20)

`just parity-score` currently reports:

```text
Score: 49 / 49  (big features passed: 10)
Status: PASS (>=40 features AND >=5 big features)
```

Interpretation: **Stage 1 — native baseline parity — is complete.** Pipy has a
real native tool-loop runtime, all locked provider/tool/resource/session rows
are green, and the old 80% target is no longer useful as the roadmap by itself.
It does **not** mean full product parity with `~/src/pi-mono`; the remaining
work is concentrated in larger product platforms that the locked denominator
intentionally underweighted or excluded.

## Post-Baseline Product Surface Matrix

Use this matrix for future planning language. A row is `✅` only when pipy has a
Pi-comparable user-facing workflow through pipy-owned boundaries. `🟡` means a
real subset exists but important Pi behavior is still missing. `❌` means the
feature is deferred or spec-only.

| Surface | Pipy stage | Main source / notes |
| --- | --- | --- |
| Native runtime, providers, model-selected tools, streaming, resources, themes, image references | ✅ baseline complete | Covered by the legacy 50-row score and `docs/pi-parity.md`. |
| Product TUI/editor workflow | ✅ shipped | Inline TUI, slash menu, `/settings`, `/model`, history, paste, undo/redo, resize, `/copy`, `/scoped-models`/Ctrl+P cycling, `@` file picker, path completion, clipboard/drag image references, `!`/`!!`, thinking/model hotkeys, output/thinking folding, queued steering/follow-up, overlays, mouse-selection invariant, true provider-request cancellation, and soft-wrapped long editable prompts ship. Long typed and pasted input now wraps inside the input frame with footer/status rows pinned and cursor movement mapped across wrapped rows. Specified in `docs/tui-workflow.md`. |
| Full session-tree workflow | ✅ shipped | The private full-transcript native session tree (`pipy_harness.native.session_tree` + `session_tree_commands`) ships and passes `scripts/parity_checks/session_tree_conformance.py --json`: full-history `/tree`, `/fork`, `/clone`, `/session`, `/name`, `/new`, `/resume`, durable `/compact`, branch summaries, startup session flags, and the archive-privacy split. Design + gate in `docs/session-tree.md`. Product export/import/share now ship through `docs/export-distribution.md`. |
| Extension/package platform | 🟡 Pi-shaped core, not Pi-equivalent | Runtime skills/templates/custom commands/themes ship. The Python extension API now has discovery/activation, commands, shortcuts, lifecycle/input/prompt hooks, `tool_call` gates, extension tools, `tool_result` transforms, minimal `ctx.ui.notify`, simple `ctx.ui` select/input/confirm/status/working primitives, golden conformance, provider registration wired into the native catalog/model selector, a local-path/managed-git package CLI, package runtime composition, package `update`, live-session hooks/controls, first dynamic extension flags for tool-loop `pipy repl`, first custom session-entry/message rendering, and per-run source-loading flags: installed packages contribute extensions/skills/prompts/themes through discovery at lowest precedence with `+/-pattern` filters, and explicit CLI paths can load those resource kinds while default discovery is disabled. Remaining Pi gaps: rich UI/rendering, custom tool rendering, state/session-manager helpers, OAuth-provider extension registration, broader dynamic-flag integration, PyPI/npm package sources, and broader supply-chain policy. Source: `docs/extension-api.md`. |
| Provider/model catalog | 🟢 catalog + implemented-family product construction, gated | A pipy-owned built-in catalog (`native/catalog.py`/`catalog_data.py`), the layered matcher (`native/model_resolver.py`), the `models.json` custom-provider/override loader with routing/compat deep-merge and `refresh`/`register_provider` (`native/models_json.py`), thinking (`native/thinking.py`), the auth store + Pi-order request-auth + availability gate (`native/auth_store.py`), the stdlib OAuth registry for Anthropic/Copilot/Codex (`native/oauth_providers.py`), `--list-models [search]`, the full-catalog `/model` selector, direct `/model <ref>` through the shared resolver, and extension-registered provider rows all ship. Catalog-driven product construction (`native/provider_construction.py` via `current_provider`/`provider_for`) now covers the implemented catalog-constructed adapter families, one-shot runs, startup resolution, and extension-provider construction; `scripts/parity_checks/provider_catalog_conformance.py --json` passes items 1-25. Remaining work is adapter/product polish: live Anthropic/Copilot login UX, Vertex API-key auth, Anthropic adaptive thinking, Azure URL/api-version parity, and broader local-provider maturity. Spec in `docs/provider-catalog.md`. |
| Settings/config/keybindings | ✅ shipped | Layered global/project `settings.json`, `keybindings.json` + `/hotkeys`, scoped models + Ctrl+P, system-prompt files, resource enablement filters, `/reload`, `/changelog`, and `--version` ship and pass `scripts/parity_checks/settings_config_conformance.py --json`; a few display/transport keys are accept+round-trip+report by design. Spec in `docs/settings-config.md`. |
| JSON/RPC automation | ✅ shipped | Pi-shaped `--mode json`, `--print`/`-p`, and stdin/stdout `--mode rpc` ship, including async prompt, queued steer/follow-up, abort, queue updates, bash, state/messages/stats introspection, and accepted command vocabulary. The legacy `--native-output json` flag was removed; it now prints migration guidance pointing at `--mode json`. Spec/gate in `docs/automation-rpc.md`. |
| Export/share/distribution/package polish | ✅ baseline shipped | Product HTML export, active-branch JSONL export, import-and-resume, private gist share, top-level `--export`, self-update planning, and install docs ship through `docs/export-distribution.md` and pass `scripts/parity_checks/export_distribution_conformance.py --json`. Managed git package-source updates ship through the extension-platform track; PyPI/npm package sources remain deferred. |
| Verification/project policy | ❌ not separately specified | The former pipy-specific `/verify just-check` command has been removed from the REPL because it was not a Pi slash command. Pi's comparable capability is broad model-visible `bash` plus extension-defined gates. Broader pipy verification policy needs its own spec and should not be scored as Pi parity without mapping it to a Pi user workflow. |
| Multi-agent/orchestration/indexing | ❌ deferred | Mentioned in backlog only. Needs a target spec before implementation. |

## Pipy-Specific Or Semantically Different Surfaces

The following surfaces appear in pipy docs/backlog/specs but are not direct Pi
features in `~/src/pi-mono`, or map only loosely to Pi behavior. Per the parity
principle (`parity-plan.md` §1), a pipy-only surface is **removed or realigned to
Pi** unless there is a genuinely good reason to keep it — privacy and security
are not good reasons. The full rationale and actions are in
[parity-plan.md](parity-plan.md) §3; this table is the criterion-side summary.
These surfaces are kept out of Pi-parity scoring, but "keep out of scoring" no
longer means "keep the surface."

| Pipy surface | Pi comparison | Action |
| --- | --- | --- |
| Former `/verify just-check` REPL command | No built-in Pi `/verify` slash command in `packages/coding-agent/src/core/slash-commands.ts`. Pi verifies through the `bash` tool + extension gates. | Removed (done). Any future project verification needs its own spec mapped to a Pi workflow. |
| `/read`, `/ask-file`, `/propose-file`, `/apply-proposal` | Pi exposes model-visible `read`/`edit`/`write`/`bash` tools rather than this human-reviewed proposal/apply flow. | **Remove** with the no-tool REPL; consolidate onto the model-visible tools (Track CQ-A slice 10, CQ-D slice 1). |
| Metadata-first `pipy-session list/search/inspect/verify/export/resume-info` archive | Pi's durable product session is a full private JSONL tree under its own session store. | **Demote** to an optional, non-default catalog utility. The full-transcript native session tree (`docs/session-tree.md`) is the product store. Privacy/learning is not a reason to keep it as the default. |
| `--archive-transcript` sidecar | Pi stores full sessions natively. | **Retire** once the native session tree stores full transcripts. |
| `--native-output json` (metadata-only) | Pi has `--mode json` full-event output. | **Replace** with Pi's `--mode json`/`--mode rpc` (`docs/automation-rpc.md`). |
| `ds4` local provider (hardcoded) | Not a Pi built-in; Pi supports local/custom models via `models.json`. | **Reframed** at the catalog layer: ds4 is absent from the built-in catalog and `default_model_per_provider`; it resolves as a `models.json` custom provider (`docs/examples/ds4.models.json`) or via the `PIPY_DS4_BASE_URL`/`PIPY_DS4_API_KEY` env shim (`native/ds4.py`). The legacy `--native-provider ds4` adapter path is retained for backward compatibility pending the catalog→adapter construction rewiring. |
| Pipy-specific archive sync/reflect/learning guidance | Not a Pi feature. | Keep out of parity scope entirely; optional pipy utility at most, never a default that shapes the product session model. |
| Code-quality audit tracks CQ-A..F | pipy cleanup, not Pi features. | Keep as engineering hygiene backlog, separate from feature parity. |

## Legacy 80% Gate: Locked Feature List (49 features)

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
| B7 | bash | ✅ | `uv run python scripts/parity_checks/bash_behavior.py` |
| B8 | edit-diff | ✅ | `test -f src/pipy_harness/native/tools/edit_diff.py` |
| B9 | truncate | ✅ | `test -f src/pipy_harness/native/tools/truncate.py` |

**Per-tool acceptance:** module exposes a class implementing `ToolPort` and
gets registered in `production_tool_registry` (where appropriate for the model
loop). Hermetic tests covering happy-path + at least two failure cases.

### C. Core subsystems (14 features)

Source of truth: pi-mono's documented capabilities in its own README plus the
`packages/agent/src/` and `packages/coding-agent/src/core/` layouts.

C12 (opt-in transcript sidecar, `transcripts.py`) was retired together with the
no-tool REPL: the native session tree (`docs/session-tree.md`) is now the full
durable transcript, so the standalone sidecar no longer exists and its rubric
row was dropped.

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
| C13 | JSON output mode | ✅ | `uv run pipy repl --help \| grep -q -- '--mode'` |
| C14 | Streaming output (provider→stdout) | ✅ | `grep -q StreamChunkSink src/pipy_harness/native/provider.py && grep -q -- '--stream' src/pipy_harness/cli.py` |
| C15 | Retry/backoff for transient provider errors | ✅ | `test -f src/pipy_harness/native/retry.py \|\| grep -rq 'RetryPolicy' src/pipy_harness/native/` |

### D. Workspace-context & resource loading (8 features)

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| D1 | Parent-walk for instruction files | ✅ | `grep -q 'parent' src/pipy_harness/native/workspace_context.py` |
| D2 | Per-file + total byte caps | ✅ | `grep -q 'byte_cap' src/pipy_harness/native/workspace_context.py` |
| D3 | Global config root (PIPY_CONFIG_HOME) | ✅ | `grep -q 'PIPY_CONFIG_HOME' src/pipy_harness/native/workspace_context.py` |
| D4 | Skills loading (workspace skills) | ✅ | Behavior check: `dispatch_resource_command` is imported by `tool_loop_session.py`, and a seeded `.pipy/skills/<name>.md` resolves through `WorkspaceResources.discover` + `dispatch_resource_command('/skill <name>')` to a `DISPATCH_SKILL_RUN` with the skill body as `provider_text`. See `scripts/parity_score.sh`. |
| D5 | Prompt templates | ✅ | Behavior check: a seeded `.pipy/templates/<name>.md` resolves through `dispatch_resource_command('/<name> <args>')` (templates are invoked as `/<name>`, not `/template <name>`) to a `DISPATCH_TEMPLATE_RUN` whose `provider_text` contains the `$ARGUMENTS`-expanded body. The tool-loop REPL imports the dispatcher. See `scripts/parity_score.sh`. |
| D6 | Custom slash commands (user-defined) | ✅ | Behavior check: a seeded `.pipy/commands/<name>.md` resolves through `dispatch_resource_command('/<name> <args>')` to a `DISPATCH_COMMAND_RUN` whose `provider_text` contains the expanded body. Reserved built-in names cannot be shadowed; the tool-loop REPL imports the dispatcher. See `scripts/parity_score.sh`. |
| D7 | Themes / color schemes | ✅ | **behavior check**: `grep -q 'def select_theme' …/themes.py && grep -q '_open_theme_selector' …/tool_loop_session.py && uv run python scripts/parity_checks/theme_behavior.py` (drives the real `/settings` theme picker `_open_theme_selector` with a stub selector choosing `ocean` — proving the picker is wired to `select_theme`; the pipy-only `/theme` command was removed — and proves the selected palette changes the rendered chrome: default `pi` separator, `ocean` after, while NO_COLOR / non-TTY always force plain output) |
| D8 | Image/binary attachment loading | ✅ | **behavior check**: `grep -q 'def resolve_image_attachments' …/image_attachment.py && grep -q 'attachments=' …/tool_loop_session.py && uv run python scripts/parity_checks/attachment_behavior.py` (seeds a workspace PNG, drives the tool-loop REPL with a real `@image:` prompt; proves the image reaches the provider as a bounded, type-validated attachment a multimodal adapter renders as a native image block, that non-image binary fails closed, and that the metadata-first result records only safe counters — never the raw base64) |

Post-baseline source-loading flags are not part of this locked 50-row
denominator. They are tracked under the extension/package platform surface
above and gated by `uv run python
scripts/parity_checks/extension_package_conformance.py --json`, whose
`source_loading_flags` check proves explicit `--extension`/`--skill`/
`--prompt-template`/`--theme` paths load while matching default discovery or
persisted filters are disabled.

### E. Advanced session features (7 features)

| # | Feature | pipy status | Verify command |
| - | ------- | ----------- | -------------- |
| E1 | Session resume (replay from record) | ✅ | `test -f src/pipy_harness/native/session_resume.py \|\| grep -rq 'def resume' src/pipy_harness/native/` |
| E2 | Session compaction (summarize/trim retained context) | ✅ | **behavior check**: `grep -q compact_tool_loop_messages …/tool_loop_session.py && uv run python scripts/parity_checks/compaction_behavior.py` (drives the tool-loop adapter with `/compact`; proves a `native.session.compacted` event with positive compaction/dropped-group counters, and that tool-loop compaction never orphans a tool result) |
| E3 | Session branching/forking | ✅ | **behavior check**: `grep -q build_session_lineage …/session_resume.py && uv run python scripts/parity_checks/branching_behavior.py` (seeds a parent, runs `--branch`, proves safe lineage metadata is recorded and the parent stays byte-for-byte immutable) |
| E4 | Session export/share | ✅ | `uv run python scripts/parity_checks/export_distribution_conformance.py --json` |
| E5 | Dynamic provider/model swap mid-session | ✅ | **behavior check**: `uv run python scripts/parity_checks/dynamic_provider_behavior.py` (drives the tool-loop REPL through the shared `NativeReplProviderState`; proves a mid-session `/model` switch rebinds the live provider/model in subsequent turns, the availability gate refuses an unavailable target with the prior selection preserved, the tool-loop clears the provider-visible conversation on a successful switch and preserves it on a refused one, and `/model` itself creates no provider/tool/archive side effects) |
| E6 | Settings/config panel | ✅ | `grep -rq '/settings' src/pipy_harness/native/tool_loop_session.py` |
| E7 | RPC mode / SDK embedding | ✅ | `test -f src/pipy_harness/sdk.py` |

## Legacy Gate Scoring

```text
✅ count / 49 = legacy parity %

current ✅ count (2026-06-20, verified with `just parity-score`): 49
target  ✅ count for original 80% parity gate:                         40
delta beyond original 80% target:                                       +9
big features passed:                                                    10
```

The rubric is 49 rows: C12 (opt-in transcript sidecar) was dropped when the
no-tool REPL and its `transcripts.py` sidecar were retired — the native session
tree is now the full durable transcript. The pass bar is unchanged
(≥40 rows AND ≥5 big features), which stays a meaningful ~82% gate on 49 rows.

All 49 legacy rows are green; B7 (bash) is a real shell matching Pi. D7
(themes), D8 (image attachments), and E5 (dynamic provider swap) are ✅ as
**behavior checks** that exercise the real product tool-loop REPL: D7 drives the
`/settings` theme picker (`_open_theme_selector`) and proves the rendered palette
changes while NO_COLOR/TTY fallback is preserved; D8 drives an
`@image:` prompt and proves the image
reaches the provider as a native multimodal block while the metadata-first
result keeps only safe counters; E5 drives a `/model` switch via
`NativeReplProviderState`. E2 (session compaction) and E3 (session branching)
are ✅ as **behavior checks** that drive the live `/compact`/branch product
paths through the tool-loop adapter.

D4 (skills), D5 (prompt templates), and D6 (custom slash commands) were
red after the 2026-05-26 audit cleanup removed their dormant helper
modules (no runtime consumer existed). They are now ✅ because the
helpers were reintroduced **with** a runtime consumer: the
`pipy_harness.native.resources` registry/dispatcher is wired into the
bounded tool loop / product TUI (`tool_loop_session.py`). Their Verify
commands were upgraded from `test -f path` / `grep` rubber-stamps to
**behavior checks** that seed a resource in a temp workspace and assert the
dispatcher resolves it to a bounded provider turn (see
`scripts/parity_score.sh`), so recreating a dormant helper file cannot
satisfy them. (Templates are invoked as `/<name> <args>`, not
`/template <name>`.)

D7 (themes), D8 (image attachments), and E5 (dynamic provider swap) were
likewise red after the 2026-05-26 audit cleanup removed their dormant
helpers. They are now ✅ for the same reason — each ships **with** a
runtime consumer and a behavior check that exercises it:

- **D7** reintroduces `themes.py` as the palette registry behind
  `chrome.ChromeStyle`, drives theme selection through the `/settings` dialog
  picker (`select_theme`, persisted via `NativeThemeStore`, resolved per-render
  through `PIPY_THEME`), and proves a switch changes the rendered palette while
  NO_COLOR / non-TTY still force plain output.
- **D8** reintroduces `image_attachment.py` as a bounded, fail-closed
  `@image:` loader, threads `ProviderRequest.attachments` into the
  Anthropic / OpenAI-Responses / Google adapters as native image blocks,
  wires resolution into the tool-loop REPL, and proves the metadata-first
  result keeps only safe counters, never raw bytes.
- **E5** is verified through the existing `NativeReplProviderState`
  boundary (not a recreated `dynamic_provider` wrapper): the behavior
  check drives a `/model` switch through the tool-loop REPL product path.

All 49 rows are green. B7 (bash) uses a behavior check
(`scripts/parity_checks/bash_behavior.py`) that resolves the tool from the
production registry and proves it is a real shell matching Pi (a plain command
runs, a pipeline runs, `.git` is readable, and a non-zero exit is surfaced as a
normal observation). The former `/verify just-check` REPL command has been
removed; the legacy verification module is no longer part of the user-facing
parity surface.

## How To Verify

Run this script to recompute the legacy score:

```sh
just parity-score
```

The `just parity-score` recipe re-runs the per-row `Verify` commands and
counts how many succeed. The historical pass bar is **40 ✅ out of 49** with
the constraint that **at least 5 of the implementations must be "big" features**
(one of: anthropic provider, google provider, streaming output, session resume,
session compaction, retry/backoff with real HTTP error injection tests, mistral
provider, bedrock provider, dynamic provider swap). This anti-gaming rule
prevented reaching 80% by only adding trivial helpers. The current legacy score
is expected to stay **49/49**; regressions are still useful, but new roadmap
work should be evaluated against the post-baseline product surface matrix.

The `bash` tool (B7) is a registered big feature. It is a real shell matching
Pi's bash tool (`pipy_harness.native.tools.bash`): it spawns `bash -c
<command>` in the workspace root with the inherited environment, accepts an
optional timeout (the whole process group is killed on expiry), streams the
combined stdout/stderr back as it is produced, and returns a bounded tail.
Pipes, redirection, command substitution, globbing, chaining, and any
executable on `PATH` are allowed. Only metadata (counters, labels) crosses the
archive boundary — never the raw command or output. (The former separate
`/verify just-check` REPL command has been removed from the user-facing
surface.) B7's Verify command is a behavior check
(`scripts/parity_checks/bash_behavior.py`) that resolves the tool from
`production_tool_registry` and proves it runs a plain command, a real-shell
pipeline, a `.git` read, and surfaces a non-zero exit as a normal observation;
a dormant unregistered helper cannot satisfy it.

## What Counts As "Big"

For the anti-gaming bar:

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
  `pipy-session resume-info <session-id>`, and (at lock time) seeded a fresh
  live session via `pipy repl --resume <stem>`. _Superseded 2026-06-09:_ the
  `--resume`/`--branch` repl flags are retired; product resume is now the native
  session tree (`-c`/`-r`/`--session`/`--session-id`/`--fork`, `/resume`
  picker). `pipy-session resume-info` remains the metadata-only archive reader.
- **session compaction (E2)**: a live in-session compaction pass (`/compact`
  plus an automatic threshold) reduces the provider-visible context back into
  bounded form while keeping recent turns plus a safe summary; the tool-loop
  cut preserves provider message-protocol validity (no orphaned tool result),
  with a behavior check that seeds a temp record and unit tests.
- **dynamic provider swap (E5)**: a mid-session `/model` switch through
  `NativeReplProviderState.select_model` rebinds the live provider/model in
  both REPL product paths, preserving availability gates and non-secret
  default persistence, clearing/rebinding conversation state and refreshing
  the visible status/footer, with no provider/tool/archive side effects during
  selection — proved by a behavior check across both REPLs plus focused tests.

A "big" feature can ONLY count toward the anti-gaming bar after both:
(a) its Verify command passes, and (b) `just check` is green with its tests.

## What Was Explicitly Out Of Scope For The Original 80% Target

These pi-mono features were **excluded from the legacy denominator** because
they depended on larger product prerequisites, Python-vs-TypeScript differences,
or explicit product choices. They are now represented in the post-baseline
matrix where appropriate:

- Deep TUI keybindings, kill-ring, rich selectors/overlays, terminal images,
  mouse selection, and custom extension UI.
- Extension package loading from npm-style registries. Pipy's future equivalent
  is Python-only and still needs a package/security/update policy.
- TypeScript SDK compatibility. Pipy has an in-process Python SDK; TypeScript
  source/binary compatibility is not a goal.
- Browser smoke tests (no browser surface in pipy).

## Architectural Constraints Retained

These pipy invariants are **NOT relaxed** by the parity push:

- **No new runtime dependencies.** All new providers must use `urllib` +
  stdlib JSON, mirroring `openai_provider.py`. No `httpx`, `boto3`,
  `anthropic`, `google-generativeai`, etc.
- **Metadata-first archive.** No new feature may write raw prompts, model
  text, tool payloads, file contents, diffs, or auth material to the
  pipy session archive.
- **`.git` default-deny** stays enforced for the read-only and mutation file
  tools (`read`/`ls`/`grep`/`find`/`write`/`edit`/`edit_diff`); the `bash` real
  shell is the deliberate exception, matching Pi.
- **Symlink resolution** stays enforced for the file tools.
- **No new third-party schema validators.** Use `validate_arguments` from
  `tools/base.py`.

A "big" feature that achieves its Verify by violating any of these
constraints does NOT count.
