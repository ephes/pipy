# Pipy → Pi Real Parity Plan

Status: parity plan written 2026-06-02 against the local Pi reference at
`/Users/jochen/src/pi-mono` (installed binary `pi 0.78.0`).

This document is the single clear plan for reaching **real feature parity** with
Pi. It is the index that ties together the per-topic specs and the cleanup work.
The latest ranked comparison snapshot against the local Pi checkout is
[Pi-Mono Gap Audit](pi-mono-gap-audit.md); use that page for slice selection
when a fresh "what is biggest now?" answer is needed. Use this plan to answer
two questions at any time:

1. What does Pi do that pipy does not yet do? (the parity gaps)
2. What does pipy do that Pi does not do, and should it stay? (the accidental
   surfaces)

It supersedes the older "we diverge for privacy" framing wherever that framing
was used to justify not matching Pi.

## Guiding Principle

Pipy is a Python slopfork of Pi. The product target is **Pi-class capability
with Pi-equivalent behavior**, reached through pipy-owned Python boundaries.

Two rules drive every decision below:

- **Match Pi.** A surface that exists in Pi is a parity target until pipy has a
  comparable end-user workflow. Pi command names, flags, modes, session
  semantics, and data captured are the reference.
- **Remove pipy-only accretions.** A surface that exists only in pipy and not in
  Pi is removed from the product (and from the docs/specs that present it as a
  feature) unless there is a genuinely good reason to keep it. **Privacy and
  security are explicitly not good reasons.** Pi stores full session
  transcripts, streams full session events, and exports full sessions; pipy's
  "metadata-first" posture is a pipy preference, not a parity virtue, and must
  not be used to justify diverging from Pi.

### Architectural constraints that are NOT divergences

These stay in force. They are engineering constraints on *how* pipy reaches
parity, not feature differences from Pi, so they are never grounds to skip a
parity target:

- **Python, pipy-owned boundaries.** Not a TypeScript port, not a wrapper around
  Pi/Codex/Claude. Pi's lifecycle, names, and semantics are the reference; the
  implementation is idiomatic Python.
- **Standard-library-first, no new runtime dependencies.** `urllib` + stdlib
  `json` for providers, stdlib for everything else. No pydantic, jsonschema,
  attrs, httpx, boto3, vendor SDKs, or TUI frameworks in the runtime.
- **Credential hygiene.** Auth tokens, API keys, OAuth refresh material, and
  secrets are never written to session files, event streams, exports, or shared
  artifacts. This is standard hygiene that Pi also observes — it is not the
  "metadata-first" divergence and does not reduce captured conversation content.

## 1. Slash-command parity matrix

Pi's built-in slash commands (source:
`packages/coding-agent/src/core/slash-commands.ts`, 21 commands) versus pipy.

| Pi command | Purpose | Pipy status | Target spec |
| --- | --- | --- | --- |
| `/settings` | Open settings menu | ✅ interactive dialog | [settings-config.md](settings-config.md) |
| `/model` | Select model (selector UI) | ✅ | [provider-catalog.md](provider-catalog.md) |
| `/scoped-models` | Enable/disable models for Ctrl+P cycling | ✅ command (view/set/clear/cycle) + Ctrl+P | [settings-config.md](settings-config.md) |
| `/export` | Export session (HTML default, `.html`/`.jsonl`) | ❌ (only metadata-only `pipy-session export`) | [export-distribution.md](export-distribution.md) |
| `/import` | Import + resume a session from JSONL | ❌ missing | [export-distribution.md](export-distribution.md) |
| `/share` | Share session as a secret GitHub gist | ❌ missing | [export-distribution.md](export-distribution.md) |
| `/copy` | Copy last agent message to clipboard | ✅ shipped | — (no spec needed) |
| `/name` | Set session display name | ❌ missing | [session-tree.md](session-tree.md) |
| `/session` | Show session info and stats | 🟡 pipy has `/status` (different shape) | [session-tree.md](session-tree.md) |
| `/changelog` | Show changelog entries | ✅ command + startup display | [settings-config.md](settings-config.md) |
| `/hotkeys` | Show all keyboard shortcuts | ✅ rendered from the resolved keybinding manager | [settings-config.md](settings-config.md), [tui-workflow.md](tui-workflow.md) |
| `/fork` | New fork from a previous user message | ❌ missing | [session-tree.md](session-tree.md) |
| `/clone` | Duplicate current session at current position | ❌ missing | [session-tree.md](session-tree.md) |
| `/tree` | Navigate session tree (switch branches) | ❌ missing | [session-tree.md](session-tree.md) |
| `/login` | Configure provider authentication | ✅ (openai-codex) | [provider-catalog.md](provider-catalog.md) |
| `/logout` | Remove provider authentication | ✅ (openai-codex) | [provider-catalog.md](provider-catalog.md) |
| `/new` | Start a new session | 🟡 pipy has `/clear` (different shape) | [session-tree.md](session-tree.md) |
| `/compact` | Manually compact session context | ✅ (durable replay pending) | [session-tree.md](session-tree.md) |
| `/resume` | Resume a different session | 🟡 metadata-only today | [session-tree.md](session-tree.md) |
| `/reload` | Reload keybindings/extensions/skills/prompts/themes | ✅ re-reads settings/keybindings/resources/theme | [settings-config.md](settings-config.md) |
| `/quit` | Quit | ✅ shipped (`/quit`, `/exit`) | — (no spec needed) |

**Pipy-only commands (not in Pi → remove or realign — see §3):** `/clear`,
`/status`, `/theme`, `/skill`, `/template`, the no-tool proposal/apply commands
`/read` `/ask-file` `/propose-file` `/apply-proposal`, and `/help` (Pi uses
`/hotkeys`).

> **Update:** the session-tree workflow has since shipped and passes
> `scripts/parity_checks/session_tree_conformance.py --json`, so `/session`,
> `/name`, `/new`, `/tree`, `/resume`, `/fork`, `/clone`, and durable `/compact`
> are now delivered (rows above predate that landing). Remaining work for those
> is realigning the pipy-only `/status`→`/session`, `/clear`→`/new` naming (§3)
> and the picker-control / branch-summary polish tracked in
> [session-tree.md](session-tree.md).

## 2. CLI flag / mode parity matrix

Reference note: this matrix is validated against `pi --help` on the installed
`pi 0.78.0` binary, which is newer than the designated source checkout at
`/Users/jochen/src/pi-mono` (commit `7c2775f6`, 2026-05-26, monorepo
`0.0.3`). Three flags below (`--session-id`, `--name/-n`,
`--exclude-tools/-xt`) exist in the 0.78.0 binary but not yet in that source
checkout's `packages/coding-agent/src/cli/args.ts`; they are real Pi flags and
stay parity targets. Everything else is present in both. Source for the rest:
`packages/coding-agent/src/cli/args.ts`.

| Pi flag / mode | Pipy status | Target spec |
| --- | --- | --- |
| `--mode text\|json\|rpc` | ❌ (pipy only has metadata-only `--native-output json`) | [automation-rpc.md](automation-rpc.md) |
| `--print, -p` (one-shot) | 🟡 `pipy run` is the one-shot path | [automation-rpc.md](automation-rpc.md) |
| `@files...` and positional `[messages...]` | 🟡 pipy has `@path`/`@image:` refs; no positional messages | [automation-rpc.md](automation-rpc.md), [tui-workflow.md](tui-workflow.md) |
| `--continue, -c` | ❌ missing | [session-tree.md](session-tree.md) |
| `--resume, -r` (picker) | 🟡 metadata-only | [session-tree.md](session-tree.md) |
| `--session <path\|id>` | ❌ missing | [session-tree.md](session-tree.md) |
| `--session-id <id>` (0.78.0; not in source checkout) | ❌ missing | [session-tree.md](session-tree.md) |
| `--fork <path\|id>` | ❌ (pipy `--branch` is metadata-only, different) | [session-tree.md](session-tree.md) |
| `--session-dir <dir>` | ❌ missing | [session-tree.md](session-tree.md) |
| `--no-session` | ❌ missing | [session-tree.md](session-tree.md) |
| `--name, -n <name>` (0.78.0; not in source checkout) | ❌ missing | [session-tree.md](session-tree.md) |
| `--models <patterns>` (Ctrl+P cycling) | ✅ `--models` overrides `enabledModels` for the session; `/scoped-models` + live Ctrl+P cycling ship (per-pattern `:level` initial preference deferred) | [settings-config.md](settings-config.md), [tui-workflow.md](tui-workflow.md) |
| `--provider` / `--model` / `--api-key` | ✅ pipy-native provider/model equivalents route through the shared catalog resolver; `--api-key` reaches catalog-backed REPL, one-shot, and implemented non-completions product calls | [provider-catalog.md](provider-catalog.md) |
| `--list-models [search]` | ✅ shipped | [provider-catalog.md](provider-catalog.md) |
| `--thinking <level>` | 🟡 mapped into catalog-backed product requests where the adapter supports a thinking shape; Google/Vertex per-model `thinkingConfig` and Anthropic adaptive-thinking shape remain adapter follow-ons | [provider-catalog.md](provider-catalog.md) |
| `--tools, -t` / `--no-tools, -nt` / `--no-builtin-tools, -nbt` / `--exclude-tools, -xt` (`-xt` is 0.78.0; not in source checkout) | ❌ missing | [settings-config.md](settings-config.md) |
| `--system-prompt` / `--append-system-prompt` | ✅ replace + repeatable append (text or file) + SYSTEM.md/APPEND_SYSTEM.md | [settings-config.md](settings-config.md) |
| `--extension, -e` / `--no-extensions, -ne` | ❌ missing | [extension-api.md](extension-api.md) |
| `--skill` / `--no-skills, -ns` | 🟡 discovery only (no load flag) | [settings-config.md](settings-config.md) |
| `--prompt-template` / `--no-prompt-templates, -np` | 🟡 discovery only | [settings-config.md](settings-config.md) |
| `--theme` / `--no-themes` | 🟡 `PIPY_THEME` + `/theme` (no flag) | [settings-config.md](settings-config.md) |
| `--no-context-files, -nc` | ✅ disables AGENTS/CLAUDE discovery | [settings-config.md](settings-config.md) |
| `--export <file>` | ❌ missing | [export-distribution.md](export-distribution.md) |
| `--verbose` / `--offline` | ❌ missing | [settings-config.md](settings-config.md) |
| `--help, -h` / `--version, -v` | ✅ `--help` and `--version`/`-v` (prints package version) | [settings-config.md](settings-config.md) |
| `pi install/remove/uninstall [-l]`, `update [source\|self\|pi]`, `list`, `config` (+ per-subcommand `--help`) | ❌ missing | [extension-api.md](extension-api.md), [export-distribution.md](export-distribution.md) |
| Extension-registered dynamic flags (e.g. `--plan`) via `unknownFlags` | ❌ missing | [extension-api.md](extension-api.md) |

**Pipy-only flags (not in Pi → remove or realign — see §3):**
`--repl-mode no-tool`, `--native-output json` (metadata-only), `--branch`,
`--archive-transcript`, `--input-runtime`, `--read-root(s)`, `--tool-budget`.

> **Update:** with the session-tree workflow shipped, the startup session flags
> `-c`/`--continue`, `-r`/`--resume`, `--session`, `--fork`, and `--no-session`
> are now delivered against the native session tree (conformance gate passing);
> the `❌`/`🟡` markers above predate that landing. pipy's `--branch` should be
> retired in favor of Pi's `--fork`/`/fork` (§3).

## 3. Accidental pipy-specific surfaces (remove or realign)

Per the guiding principle, these surfaces exist only in pipy. Each row records
why it exists, whether the reason survives ("privacy/security" never does), and
the parity action. None of these may be cited as a reason to skip a Pi parity
target, and the docs/specs must stop presenting them as product virtues.

| Pipy surface | Why it exists | Keep? | Action |
| --- | --- | --- | --- |
| **Metadata-first `pipy-session` archive as the product session store** | Privacy preference | No (privacy is not a valid reason) | The full native session tree ([session-tree.md](session-tree.md)) is the product store. `pipy-session` is demoted to an optional, non-default, separate catalog utility that never shapes or blocks parity. Stop describing metadata-first as a parity virtue. |
| **`--archive-transcript` opt-in sidecar** | Workaround for the metadata-first default (raw turns live outside the archive) | No | Redundant once the native session tree stores full transcripts like Pi. Retire the flag; the native tree is the transcript. |
| **`--native-output json` (metadata-only)** | Privacy-limited automation output | No | Replace with Pi's `--mode json` full-event stream and `--mode rpc` ([automation-rpc.md](automation-rpc.md)). Retire the metadata-only mode. |
| **No-tool REPL mode (`--repl-mode no-tool`)** | Bootstrap before the model-driven tool loop existed | No | Pi has one interactive mode with model-visible tools. Fold into the single tool-loop product session; retire the separate no-tool mode. |
| **`/read` `/ask-file` `/propose-file` `/apply-proposal`** | No-tool-REPL human-mediated proposal/apply flow | No | Pi uses model-visible `read`/`edit`/`write`/`bash`. Remove with the no-tool REPL; the archive-side parallel tool family that backs them goes too (Track CQ-A slice 10). |
| **`/verify just-check`** | pipy-specific verification command | Already removed | Done. Pi verifies via the `bash` tool + extension gates. No separate verify command returns without its own spec. |
| **`/clear`** | Local conversation reset | No | Pi has no `/clear`. Realign to Pi's `/new` (new session) and `/compact`. |
| **`/status`** | Local state readout | No | Realign to Pi's `/session` (info/stats). |
| **`/theme` slash command** | pipy theme switcher | Realign | Pi has `--theme` discovery + theme selection inside settings, not a `/theme` command. Move theme selection under `/settings`; keep `--theme`/`--no-themes` load flags. |
| **`/skill <name>` and `/template <name>` dispatcher commands** | pipy resource dispatch | Realign | Pi auto-injects skills into the system prompt and invokes prompt templates as `/<template-name>` directly. Match Pi's model: drop the `/skill`/`/template` wrappers, inject skills, register templates as their own slash commands. |
| **`/help`** | grouped command reference | Realign | Pi uses `/hotkeys` + the slash menu. Provide `/hotkeys`; keep `/help` only as an optional alias if desired. |
| **Hardcoded `ds4` built-in provider** | First local-model integration | Mostly realigned | ds4 is absent from the built-in catalog and resolves as a `models.json` custom-provider preset (`docs/examples/ds4.models.json`) or env shim. A legacy `--native-provider ds4` adapter path remains for compatibility while construction moves fully through the catalog ([provider-catalog.md](provider-catalog.md)). |
| **`--read-root(s)` cross-repo read flag** | pipy convenience for reading sibling repos | Verify | Pi reads within `cwd` + context discovery. This is a genuine non-privacy convenience; keep only if it maps to a real Pi workflow, otherwise demote. Flagged for review. |
| **`--tool-budget`** | bounds the model loop | Verify | Pi bounds turns internally without a user flag. Reasonable as an internal default; reconsider exposing it as a user flag. |
| **`--input-runtime plain\|prompt-toolkit\|auto`** | pipy input-adapter selection | Verify | Internal mechanism, not a Pi surface. Keep as an implementation detail; it need not be a documented parity feature. |
| **Archive sync / reflect / cross-agent learning guidance** | pipy learning/catalog layer (privacy-scoped) | No (as a parity item) | Not a Pi feature. Keep out of parity scope entirely; if retained at all it is an optional pipy utility, never a default that shapes the product session model. |
| **Code-quality audit tracks CQ-A..F** | pipy engineering hygiene | Keep (non-feature) | Internal cleanup, not a Pi feature and not user-facing. Stays in the backlog as engineering work, separate from parity. |
| **Persistent cross-session prompt history (`PromptHistoryStore`)** | pipy editor convenience | Verify | Confirm against Pi's editor history behavior; if Pi has no equivalent it is a small pipy extra — keep only if cheap and clearly useful, otherwise drop. |

## 4. Big topics and their specs

Each large parity surface has (or now has) a detailed spec with a goal,
invariants, milestone slices, and a deterministic conformance gate. Status is
the product state, not the spec state.

| Topic | Spec | Product status | Conformance gate |
| --- | --- | --- | --- |
| Native runtime, providers baseline, model-selected tools, streaming, workspace context | [harness-spec.md](harness-spec.md), [pi-parity.md](pi-parity.md) | ✅ baseline | `just parity-score` (legacy 50-row) |
| Full session-tree workflow (full-transcript product store, `/tree` `/fork` `/clone` `/session` `/name` `/new` `/resume`, durable compaction, startup session flags) | [session-tree.md](session-tree.md) | ✅ shipped — `pipy_harness.native.session_tree` + `session_tree_commands` pass the conformance gate (full-transcript store, branch/fork/clone, startup flags, archive-privacy split) | `scripts/parity_checks/session_tree_conformance.py --json` (passing) |
| Extension / package platform (Python extensions, tools/commands/providers/keybindings/UI hooks, install/update/list/config) | [extension-api.md](extension-api.md) | 🟡 draft spec, bounded Markdown-resource subset only | golden conformance extension (in spec) |
| Provider / model catalog (`models.json`, broad catalog, subscription auth incl. GitHub Copilot + Anthropic, thinking levels, `--list-models`, `--models` cycling) | [provider-catalog.md](provider-catalog.md) | 🟡 catalog construction closeout shipped for implemented catalog-constructed provider families, one-shot, and startup resolution; remaining work is live Anthropic/Copilot login UX, the deliberate `openai-codex-responses` legacy-factory exception, narrow adapter parity follow-ons, and extension-registered providers | `scripts/parity_checks/provider_catalog_conformance.py --json` (passes items 1-24, including product construction for Chat Completions, non-completions families, one-shot, and startup resolution) |
| Settings / config / keybindings (global + project `settings.json`, `keybindings.json`, scoped models, system-prompt files, resource toggles, `/reload`, `/changelog`, version/update) | [settings-config.md](settings-config.md) | ✅ shipped: layered `settings.json`, `keybindings.json` + `/hotkeys`, scoped models + Ctrl+P, system-prompt files + `--no-context-files`, `pipy config` resource toggles, `/reload`, `/changelog` + `--version`; the 17-check gate passes (a few unsurfaced display/transport keys are accept+round-trip+report by design) | `scripts/parity_checks/settings_config_conformance.py --json` |
| JSON / RPC automation (`--mode json` full-event stream, `--mode rpc` protocol, steer/follow-up/abort, session switching) | [automation-rpc.md](automation-rpc.md) | 🟡 metadata-only JSON + Python SDK only | `scripts/parity_checks/automation_rpc_conformance.py --json` |
| TUI / editor workflow depth (`@` file picker — Pi uses exact/prefix/substring scoring, not fuzzy — path completion, image paste, `!`/`!!`, thinking hotkeys, folding, queued steering, mouse selection; scoped-model Ctrl+P cycling already ships via settings) | [tui-workflow.md](tui-workflow.md) | 🟡 daily-driver basics ship; **true active-turn provider-request cancellation shipped** (Escape/Ctrl-C close the in-flight `urllib`/SSE request via `CancelToken` and reap the worker, real-PTY + boundary tests) | real-PTY tests + conformance gate (in spec) |
| Export / import / share / distribution / self-update (HTML + JSONL export, import-and-resume, gist share, `--export`, `/changelog`, update flow, install docs) | [export-distribution.md](export-distribution.md) | ❌ metadata-only export only | `scripts/parity_checks/export_distribution_conformance.py --json` |
| User documentation parity (quickstart, usage, providers, settings, keybindings, sessions, customization, automation, platform setup) | [user-documentation.md](user-documentation.md) | ❌ mostly internal specs today | docs parity review checklist in spec |

**Verification / project policy** is intentionally not a separate topic: Pi has
no `/verify` command. Verification is the model-visible `bash` tool plus
extension-defined permission gates ([extension-api.md](extension-api.md)). Any
future project-defined verification policy needs its own spec mapped to a real
Pi workflow before it is treated as parity.

**Multi-agent / orchestration / indexing** remains out of scope: it is not a
core Pi single-agent feature. It needs its own target spec before any work.

## 5. Recommended sequencing

Ordering reflects dependencies and leverage, not a hard schedule. This sequence
is kept aligned with the ranked [Pi-Mono Gap Audit](pi-mono-gap-audit.md):

1. **Session-tree workflow** ([session-tree.md](session-tree.md)) — shipped.
   The full-transcript native store is now the product session source and
   unblocks `/export`, `/import`, `/share`, durable `/compact`, `--mode json/rpc`
   session events, and retiring `--archive-transcript` as follow-up cleanup.
2. **Provider / model catalog** ([provider-catalog.md](provider-catalog.md)) —
   construction closeout shipped: the catalog foundation, direct
   `/model <ref>` resolver, OpenAI-compatible Chat Completions product
   construction, non-completions construction for implemented
   catalog-constructed adapter families, `pipy run` one-shot construction, and startup
   `--native-provider`/`--native-model` resolution are all gated by the provider
   catalog conformance script. Live Anthropic/Copilot login UX, narrow adapter
   parity follow-ons, and extension-registered providers remain later work.
3. **Settings / config / keybindings** ([settings-config.md](settings-config.md))
   — ✅ shipped: layered `settings.json`, `keybindings.json` + `/hotkeys`, scoped
   models + live Ctrl+P cycling, system-prompt files + `--no-context-files`,
   `pipy config` resource toggles, `/reload`, `/changelog`, and `--version`,
   with the 17-check conformance gate passing. Remaining follow-on: live
   re-application surfaces for a few display/transport keys (editor padding,
   hardware cursor, clear-on-shrink, websocket transport, in-turn steering) that
   are currently accept+round-trip+report.
4. **User documentation parity** ([user-documentation.md](user-documentation.md))
   — can run alongside implementation tracks; pipy needs Pi-like user docs, not
   only internal planning/spec docs.
5. **TUI / editor depth** ([tui-workflow.md](tui-workflow.md)) — true
   provider-request cancellation (the correctness fix, not polish) has **shipped**;
   remaining work is the editor-comfort surface (`@` picker, path completion,
   image paste, `!`/`!!`, folding, queued steering, mouse selection).
6. **Automation: `--mode json` then `--mode rpc`**
   ([automation-rpc.md](automation-rpc.md)) — depends on the session tree for
   full session events and on the extension API for the UI channel.
7. **Export / import / share / distribution**
   ([export-distribution.md](export-distribution.md)) — depends on the session
   tree.
8. **Extension / package platform** ([extension-api.md](extension-api.md)) — the
   largest platform; many other surfaces (providers, commands, keybindings, UI,
   verification gates) gain extensibility once it lands.

Cleanup (§3) happens alongside the relevant topic: e.g. the no-tool REPL and its
proposal/apply commands retire with the single-session consolidation cleanup
that follows the shipped session tree; `--native-output json` retires with the
automation work; the transcript sidecar retires in a session-tree follow-up
cleanup once callers no longer need it.

## 6. Definition of "real parity done"

Real parity is reached when:

- Every Pi slash command in §1 has a comparable pipy workflow (or a deliberate,
  spec-justified non-Pi-divergent decision).
- Every Pi CLI flag/mode in §2 has a comparable pipy surface.
- Every big-topic conformance gate in §4 passes, and `just check` is green.
- The accidental surfaces in §3 are removed or realigned, so pipy no longer
  ships behavior that diverges from Pi purely for privacy/learning reasons.
- pipy stores, streams, and exports full session content like Pi (full native
  session tree; full `--mode json`/`rpc` events; full HTML/JSONL export), with
  only credentials/secrets withheld.
- User-facing documentation covers the same product surfaces as Pi's docs, with
  shipped behavior separated from target specs ([user-documentation.md](user-documentation.md)).

Until then, `docs/parity-criterion.md` keeps the legacy 50-row baseline score
for regression tracking, but the post-baseline matrix there and this plan define
the real remaining work.
