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
| `/export` | Export session (HTML default, `.html`/`.jsonl`) | ✅ native tool-loop `/export` writes full-tree self-contained HTML or active-branch linear JSONL | [export-distribution.md](export-distribution.md) |
| `/import` | Import + resume a session from JSONL | ✅ `/import <path.jsonl>` copies into the native store and resumes after confirmation (`--yes` for scripts) | [export-distribution.md](export-distribution.md) |
| `/share` | Share session as a secret GitHub gist | ✅ `/share` exports HTML and uploads a secret gist through stdlib GitHub API with token redaction and fakeable tests | [export-distribution.md](export-distribution.md) |
| `/copy` | Copy last agent message to clipboard | ✅ shipped | — (no spec needed) |
| `/name` | Set session display name | ✅ shipped | [session-tree.md](session-tree.md) |
| `/session` | Show session info and stats | ✅ shipped | [session-tree.md](session-tree.md) |
| `/changelog` | Show changelog entries | ✅ command + startup display | [settings-config.md](settings-config.md) |
| `/hotkeys` | Show all keyboard shortcuts | ✅ rendered from the resolved keybinding manager | [settings-config.md](settings-config.md), [tui-workflow.md](tui-workflow.md) |
| `/fork` | New fork from a previous user message | ✅ shipped | [session-tree.md](session-tree.md) |
| `/clone` | Duplicate current session at current position | ✅ shipped | [session-tree.md](session-tree.md) |
| `/tree` | Navigate session tree (switch branches) | ✅ shipped | [session-tree.md](session-tree.md) |
| `/login` | Configure provider authentication | ✅ (openai-codex) | [provider-catalog.md](provider-catalog.md) |
| `/logout` | Remove provider authentication | ✅ (openai-codex) | [provider-catalog.md](provider-catalog.md) |
| `/new` | Start a new session | ✅ shipped | [session-tree.md](session-tree.md) |
| `/compact` | Manually compact session context | ✅ durable replay shipped | [session-tree.md](session-tree.md) |
| `/resume` | Resume a different session | ✅ interactive picker overlay (search/scope/sort/named/rename/delete) + non-TTY subcommands | [session-tree.md](session-tree.md) |
| `/reload` | Reload keybindings/extensions/skills/prompts/themes | ✅ re-reads settings/keybindings/resources/theme | [settings-config.md](settings-config.md) |
| `/quit` | Quit | ✅ shipped (`/quit`, `/exit`) | — (no spec needed) |

**Pipy-only commands (not in Pi → remove or realign — see §3):** `/clear`,
`/status`, `/theme`, `/skill`, `/template`, the no-tool proposal/apply commands
`/read` `/ask-file` `/propose-file` `/apply-proposal`, and `/help` (Pi uses
`/hotkeys`).

Session-tree workflow commands (`/session`, `/name`, `/new`, `/tree`,
`/resume`, `/fork`, `/clone`, and durable `/compact`) now ship and pass
`scripts/parity_checks/session_tree_conformance.py --json`. Remaining cleanup is
limited to retiring or aliasing the older pipy-only names (`/status`, `/clear`)
and the picker-control / branch-summary polish tracked in
[session-tree.md](session-tree.md).

## 2. CLI flag / mode parity matrix

Reference note: this matrix is validated against `pi --help` on the installed
`pi 0.78.0` binary, which is newer than the designated source checkout at
`/Users/jochen/src/pi-mono` (commit `7c2775f6`, 2026-05-26, monorepo
`0.0.3`). Three flags below (`--session-id`, `--name/-n`,
`--exclude-tools/-xt`) exist in the 0.78.0 binary but not yet in that source
checkout's `packages/coding-agent/src/cli/args.ts`; they are real Pi flags and
stay parity targets. Everything else is present in both. Source for the rest:
`packages/coding-agent/src/cli/args.ts`. The full session-startup flag set below
now ships (`--session-id`/`--session-dir`/`--name`/`-n` included), with the Pi
mutual-exclusion errors and the cross-project `--session` fork prompt; the old
metadata-only `--resume RECORD`/`--branch LABEL` repl flags are retired.

| Pi flag / mode | Pipy status | Target spec |
| --- | --- | --- |
| `--mode text\|json\|rpc` | ✅ `pipy repl --mode json` (full Pi-shaped event stream) and `--mode rpc` (long-lived stdin/stdout JSONL protocol) ship; `--mode text` is the interactive/one-shot default | [automation-rpc.md](automation-rpc.md) |
| `--print, -p` (one-shot) | ✅ `pipy repl --print`/`-p "<prompt>"` prints the final assistant text; `pipy run` remains the metadata-recording one-shot path | [automation-rpc.md](automation-rpc.md) |
| `@files...` and positional `[messages...]` | 🟡 pipy has `@path`/`@image:` refs and a positional one-shot prompt for `--mode json`/`--print`; multiple positional messages still pending | [automation-rpc.md](automation-rpc.md), [tui-workflow.md](tui-workflow.md) |
| `--continue, -c` | ✅ continues the most recent native session | [session-tree.md](session-tree.md) |
| `--resume, -r` (picker) | ✅ `-r`/`--resume-session` opens the interactive startup picker on a TTY; continues most-recent on a non-TTY | [session-tree.md](session-tree.md) |
| `--session <path\|id>` | ✅ opens a native file/partial id; cross-project match prompts to fork | [session-tree.md](session-tree.md) |
| `--session-id <id>` (0.78.0; not in source checkout) | ✅ open-exact-or-create | [session-tree.md](session-tree.md) |
| `--fork <path\|id>` | ✅ forks a native file/partial id (the old metadata-only `--branch` is retired) | [session-tree.md](session-tree.md) |
| `--session-dir <dir>` | ✅ native store root override (never reuses `$PIPY_SESSION_DIR`) | [session-tree.md](session-tree.md) |
| `--no-session` | ✅ ephemeral — no native tree + no `pipy-session` record | [session-tree.md](session-tree.md) |
| `--name, -n <name>` (0.78.0; not in source checkout) | ✅ names the native session at startup | [session-tree.md](session-tree.md) |
| `--models <patterns>` (Ctrl+P cycling) | ✅ `--models` overrides `enabledModels` for the session; `/scoped-models` + live Ctrl+P cycling ship (per-pattern `:level` initial preference deferred) | [settings-config.md](settings-config.md), [tui-workflow.md](tui-workflow.md) |
| `--provider` / `--model` / `--api-key` | ✅ pipy-native provider/model equivalents route through the shared catalog resolver; `--api-key` reaches catalog-backed REPL, one-shot, and implemented non-completions product calls | [provider-catalog.md](provider-catalog.md) |
| `--list-models [search]` | ✅ shipped | [provider-catalog.md](provider-catalog.md) |
| `--thinking <level>` | 🟡 mapped into catalog-backed product requests where the adapter supports a thinking shape; Google/Vertex per-model `thinkingConfig` and Anthropic adaptive-thinking shape remain adapter follow-ons | [provider-catalog.md](provider-catalog.md) |
| `--tools, -t` / `--no-tools, -nt` / `--no-builtin-tools, -nbt` / `--exclude-tools, -xt` (`-xt` is 0.78.0; not in source checkout) | ❌ missing | [settings-config.md](settings-config.md) |
| `--system-prompt` / `--append-system-prompt` | ✅ replace + repeatable append (text or file) + SYSTEM.md/APPEND_SYSTEM.md | [settings-config.md](settings-config.md) |
| `--extension, -e` / `--no-extensions, -ne` | ✅ explicit file/dir loading + default-discovery disable; installed local-path and managed git package resources contribute at runtime | [extension-api.md](extension-api.md) |
| `--skill` / `--no-skills, -ns` | ✅ explicit file/dir loading + default-discovery disable | [settings-config.md](settings-config.md) |
| `--prompt-template` / `--no-prompt-templates, -np` | ✅ explicit file/dir loading + default-discovery disable | [settings-config.md](settings-config.md) |
| `--theme` / `--no-themes` | ✅ explicit file/dir loading + package-theme discovery disable; active theme still selected by settings, `PIPY_THEME`, or `/theme` | [settings-config.md](settings-config.md) |
| `--no-context-files, -nc` | ✅ disables AGENTS/CLAUDE discovery | [settings-config.md](settings-config.md) |
| `--export <file>` | ✅ top-level `pipy --export <session.jsonl> [output.html]` exports native sessions to HTML and exits | [export-distribution.md](export-distribution.md) |
| `--verbose` / `--offline` | ❌ missing | [settings-config.md](settings-config.md) |
| `--help, -h` / `--version, -v` | ✅ `--help` and `--version`/`-v` (prints package version) | [settings-config.md](settings-config.md) |
| `pi install/remove/uninstall [-l]`, `update [source\|self\|pi]`, `list`, `config` (+ per-subcommand `--help`) | 🟡 `pipy install/remove/uninstall [-l]`, `list`, and `config <enable\|disable> <skill\|prompt\|theme\|extension> <name>` ship for local-path and managed git sources, installed packages contribute extensions/skills/prompts/themes, package `update` refreshes managed git caches, and `pipy update self\|pipy [--force] [--dry-run]` ships for install-method-aware self-update planning. Remote PyPI/`npm:` package sources remain deferred to a broader supply-chain policy. | [extension-api.md](extension-api.md), [export-distribution.md](export-distribution.md) |
| Extension-registered dynamic flags (e.g. `--plan`) via `unknownFlags` | 🟡 landed for `pipy repl` tool-loop boolean/string flags; broader top-level/automation integration remains | [extension-api.md](extension-api.md) |

**Pipy-only flags (not in Pi → remove or realign — see §3):**
`--repl-mode no-tool`, `--native-output json` (metadata-only),
`--archive-transcript`, `--input-runtime`, `--read-root(s)`, `--tool-budget`.
(The pipy-only metadata `--resume RECORD` / `--branch LABEL` repl flags were
retired on 2026-06-09 in favor of the native session tree.)

## 3. Accidental pipy-specific surfaces (remove or realign)

Per the guiding principle, these surfaces exist only in pipy. Each row records
why it exists, whether the reason survives ("privacy/security" never does), and
the parity action. None of these may be cited as a reason to skip a Pi parity
target, and the docs/specs must stop presenting them as product virtues.

| Pipy surface | Why it exists | Keep? | Action |
| --- | --- | --- | --- |
| **Metadata-first `pipy-session` archive as the product session store** | Privacy preference | No (privacy is not a valid reason) | The full native session tree ([session-tree.md](session-tree.md)) is the product store. `pipy-session` is demoted to an optional, non-default, separate catalog utility that never shapes or blocks parity. Stop describing metadata-first as a parity virtue. |
| **`--archive-transcript` opt-in sidecar** | Workaround for the metadata-first default (raw turns live outside the archive) | No | Redundant once the native session tree stores full transcripts like Pi. Retire the flag; the native tree is the transcript. |
| **`--native-output json` (metadata-only)** | Privacy-limited automation output | No | **Done** — Pi's `--mode json` full-event stream and `--mode rpc` have shipped ([automation-rpc.md](automation-rpc.md)) and `--native-output json` is deprecated (its `--help` points to `--mode json`). The metadata-only object is retained on `pipy run` for existing callers pending final removal. |
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
| Full session-tree workflow (full-transcript product store, `/tree` `/fork` `/clone` `/session` `/name` `/new` `/resume` interactive picker, durable compaction, full startup session flag set incl. `--session-id`/`--session-dir`/`--name`, mutual exclusion, cross-project fork prompt) | [session-tree.md](session-tree.md) | ✅ shipped — `pipy_harness.native.session_tree` + `session_tree_commands` + `tui.run_session_picker` pass the conformance gate and the Pi comparison (full-transcript store, branch/fork/clone, interactive picker rows/actions, startup flags, archive-privacy split) | `scripts/parity_checks/session_tree_conformance.py --json` + `scripts/parity_checks/session_tree_pi_comparison.py --json` (passing) |
| Extension / package platform (Python extensions, tools/commands/providers/keybindings/UI hooks, install/update/list/config) | [extension-api.md](extension-api.md) | 🟡 core Pi-shaped runtime shipped, but not Pi-equivalent platform parity — discovery/inventory + activation + dispatch + core hooks + tool registration + `tool_result` transforms + `ctx.ui.notify` + shortcuts + golden conformance ext + provider registration through the native catalog (`api.register_provider`/`ProviderPort` composition, `--list-models`, startup resolution, `/model`, `/reload`), local-path and managed-git package CLI/runtime composition for extensions/skills/prompts/themes, per-run source-loading flags, package `update`, live-session hooks/controls (`user_bash`, `before_provider_request`, session-operation gates, active tool/model/thinking controls), first dynamic extension flags (`ExtensionFlag`, tool-loop `ctx.flags`), and a first custom session-entry/message-rendering slice (`api.register_message_renderer`, `ctx.append_entry`) all ship. Deferred: richer multi-widget extension UI/rendering, extension state/session-manager helpers, custom tool renderers, broader dynamic-flag integration, remote PyPI/npm sources, OAuth-provider extension registration, and the RPC extension-UI channel | the `extension_*_conformance.py` gates incl. `extension_dispatch_conformance.py --json` + `extension_providers_conformance.py --json` + `extension_package_conformance.py --json` + `extension_live_session_conformance.py --json`; golden conformance extension `extension_conformance_gate.py --json` |
| Provider / model catalog (`models.json`, broad catalog, subscription auth incl. GitHub Copilot + Anthropic, thinking levels, `--list-models`, `--models` cycling) | [provider-catalog.md](provider-catalog.md) | 🟡 catalog construction closeout shipped for implemented catalog-constructed provider families, one-shot, startup resolution, and extension-registered provider rows; remaining work is live Anthropic/Copilot login UX, the deliberate `openai-codex-responses` legacy-factory exception, and narrow adapter parity follow-ons | `scripts/parity_checks/provider_catalog_conformance.py --json` (passes items 1-25, including product construction for Chat Completions, non-completions families, one-shot, startup resolution, and extension-provider catalog wiring) |
| Settings / config / keybindings (global + project `settings.json`, `keybindings.json`, scoped models, system-prompt files, resource toggles, `/reload`, `/changelog`, version/update) | [settings-config.md](settings-config.md) | ✅ shipped: layered `settings.json`, `keybindings.json` + `/hotkeys`, scoped models + Ctrl+P, system-prompt files + `--no-context-files`, `pipy config` resource toggles, `/reload`, `/changelog` + `--version`; the 17-check gate passes (a few unsurfaced display/transport keys are accept+round-trip+report by design) | `scripts/parity_checks/settings_config_conformance.py --json` |
| JSON / RPC automation (`--mode json` full-event stream, `--mode rpc` protocol, steer/follow-up/abort, session switching) | [automation-rpc.md](automation-rpc.md) | ✅ `--mode json`, `--print`, and `--mode rpc` ship (async prompt, steer/follow-up queued and delivered as the next run after the active turn settles (abort discards that run's queued steering), queue updates, bash, state/messages/stats introspection, all 29 commands accepted); true in-turn steering injection, native/socket daemon, RPC extension-UI channel, and full fork/clone/switch over RPC remain follow-ons | `scripts/parity_checks/automation_rpc_conformance.py --json` |
| TUI / editor workflow depth (`@` file picker — Pi uses exact/prefix/substring scoring, not fuzzy — path completion, image paste, `!`/`!!`, thinking hotkeys, folding, queued steering, mouse selection; scoped-model Ctrl+P cycling already ships via settings) | [tui-workflow.md](tui-workflow.md) | 🟡 daily-driver basics ship; long editable prompts now soft-wrap with cursor mapping and pinned footer/status, and **true active-turn provider-request cancellation shipped** (Escape/Ctrl-C close the in-flight `urllib`/SSE request via `CancelToken` and reap the worker, real-PTY + boundary tests) | real-PTY tests + conformance gate (in spec) |
| Export / import / share / distribution / self-update (HTML + JSONL export, import-and-resume, gist share, `--export`, `/changelog`, update flow, install docs) | [export-distribution.md](export-distribution.md) | ✅ baseline shipped: product HTML/JSONL export, import, share, top-level `--export`, self-update planning, install docs, and bare `pipy update` composition with the extension-package update half | `scripts/parity_checks/export_distribution_conformance.py --json` |
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
was groomed on 2026-06-15 after the native session tree, session CLI/pickers,
settings/keybindings, TUI workflow, provider-catalog construction, and
JSON/RPC automation tracks had all shipped. Those shipped foundations stay in
§4 as conformance gates, but they are no longer the next large implementation
topics.

1. **Extension / package platform follow-ons**
   ([extension-api.md](extension-api.md)) — core local automation plus
   local-path and managed git package runtime composition and package `update`
   have landed, but pipy is still not a Pi-equivalent package platform.
   Remaining work includes rich extension UI/rendering, broader dynamic-flag
   integration, future PyPI/npm package sources behind a broader supply-chain policy,
   OAuth-provider extension registration, extension state/session-manager
   helpers, and the RPC extension-UI channel. Live-session hooks for user bash,
   provider-request transforms, session-operation gates, and dynamic active
   tool/model/thinking controls have landed.
2. **User documentation parity** ([user-documentation.md](user-documentation.md))
   — run in parallel with implementation. Pipy needs outside-in product docs,
   not only internal specs, and those docs should track shipped behavior rather
   than planned parity.
3. **Provider / model catalog follow-ons** ([provider-catalog.md](provider-catalog.md))
   — continue as focused adapter slices: live Anthropic/Copilot login UX,
   Vertex API-key auth, Anthropic adaptive thinking, Azure URL/api-version
   parity and broader local-provider maturity.
4. **Top-level CLI compatibility and parity cleanup** — stage alongside the
   owning topics. Realign the harness-shaped `auth|run|repl` surfaces where Pi
   has one top-level product command, remove or hide pipy-only internals such as
   `--archive-transcript`, no-tool REPL/proposal commands, `/clear`, `/status`,
   `/theme`, `/skill`, `/template`, `/help`, `--native-output json`, and
   exposed implementation flags unless a real Pi workflow justifies them.
7. **Verification / project policy through extensions** — do not revive the
   removed pipy-only `/verify` command. Richer verification and permission gates
   should be expressed as extension tools/hooks after the extension platform
   exists.

Cleanup (§3) happens alongside the relevant topic: the no-tool REPL and its
proposal/apply commands retire with single product-session consolidation;
`--native-output json` retires after callers move to `--mode json`; the
transcript sidecar retires once the native tree/export surfaces cover its use
cases; and resource wrapper commands realign when extension/resource loading has
Pi-shaped command registration.

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
