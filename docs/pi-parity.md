# Pi Parity And Differences

Status: current slopfork map for the Python `pipy` runtime compared with the
local Pi reference in `/Users/jochen/src/pi-mono`.

Pipy is a Python slopfork inspired by Pi. The goal is Pi-class local
coding-agent usefulness through pipy-owned Python boundaries, not a literal
port of Pi's TypeScript packages, terminal UI, storage model, extension system,
or command names.

## What Has Been Slopforked

Status labels are intentionally coarse:

- Implemented: the named capability exists for pipy's current architecture.
- Partial: pipy has a bounded subset of the Pi behavior.
- Narrow first slice: pipy has the first reviewed boundary, not the general Pi
  capability.
- Different foundation: pipy solves the same product need through a deliberately
  different storage or architecture model.
- Support path: implemented for capture/reference work, not the product
  runtime.

| Pi idea | Pipy state | Notes |
| --- | --- | --- |
| Local-first terminal coding agent | Partial | `pipy` and `pipy repl` start a native shell in the current workspace. It is still line-oriented, not a full TUI. |
| Direct provider access | Implemented foundation | `ProviderPort` supports fake, OpenAI API-key Responses, OpenAI Codex subscription, and OpenRouter providers. |
| OpenAI Codex subscription auth | Implemented as separate provider | Pipy uses its own OAuth state under `${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`, modeled on Pi's Codex OAuth shape, and does not read Pi credentials. |
| `/login`, `/logout`, `/model` | Implemented narrow shell commands | Commands are local, late-bind provider selection, and do not create provider turns or archive auth material. |
| Startup orientation | Implemented first pass | The shell prints compact startup chrome with controls, resource labels, and safe status labels. The next UI slice is a Pi-like visual/resource-label pass. |
| No approval popups for normal interactive read/context commands | Implemented | Explicit user-entered `/read`, `/ask-file`, and `/propose-file` commands use non-interactive safety checks rather than visible approval prompts. |
| Read tool | Partial | `/read <path>` supports one explicit, bounded, UTF-8 workspace-relative excerpt per REPL session. No broad model-selected read tool exists yet. |
| Provider-visible file context | Partial | `/ask-file <path> -- <question>` forwards one bounded excerpt only in memory to one provider turn. |
| Proposal flow | Partial | `/propose-file <path> -- <change-request>` forwards one bounded excerpt and can retain a same-session proposal draft. |
| Write/edit capability | Narrow first slice | `/apply-proposal <path>` applies one same-session, human-reviewed, one-file proposal through `NativePatchApplyTool`. There is no general write/edit tool yet. |
| Verification after changes | Narrow first slice | `/verify just-check` runs only the allowlisted internal `just check` command after a successful same-session apply. |
| Session records | Different foundation implemented | Pipy writes metadata-first JSONL plus optional Markdown under `~/.local/state/pipy/sessions`; Pi stores full tree sessions under its own agent state. |
| Search/inspect/reflect | Implemented for pipy records | `pipy-session list/search/inspect/verify/reflect` operates over finalized metadata records, not full transcripts. |
| Print-like one-shot mode | Partial | `pipy run --agent pipy-native` runs one native turn; default stdout is successful final text only, and `--native-output json` gives metadata-only automation output. |
| Subprocess wrapping | Implemented as support path | `pipy run --agent custom|codex|claude|pi -- ...` records conservative lifecycle metadata around another command, but this is not the product runtime. |

## Still To Slopfork

The main missing Pi-class surfaces are intentionally deferred until the current
metadata and boundary invariants are stable:

- Full interactive terminal UI with editor, footer, model/status controls,
  overlays, selectors, resize handling, and richer startup presentation.
- File references with `@`, path completion, multiline editing, pasted images,
  and keyboard shortcut handling.
- Model-selected tool loop with read, write, edit, bash, and follow-up tool
  observations.
- Multiple file/context reads per session and broader context/resource loading.
- AGENTS/CLAUDE-style context discovery beyond the static labels currently
  shown by startup chrome.
- Prompt templates, skills, themes, extensions, and package loading.
- Session resume, branch/tree navigation, fork, compaction, export, and share.
- RPC mode and SDK embedding surfaces.
- Provider registry and broad provider/model catalog.
- Cost/context/token footer behavior beyond safe usage counters.
- Arbitrary shell command support and non-allowlisted verification.

## Architecture Differences From Pi

```mermaid
flowchart LR
  subgraph Pi[Pi reference architecture]
    PiModes[Interactive, print, RPC, SDK modes]
    PiAgentSession[AgentSession]
    PiAgent[pi-agent-core Agent]
    PiAI[pi-ai providers and models]
    PiTools[read, write, edit, bash tools]
    PiSessions[Tree JSONL sessions]
    PiExt[Extensions, skills, prompts, themes]
  end

  subgraph Pipy[Pipy current architecture]
    PipyCLI[pipy CLI]
    PipyRunner[HarnessRunner]
    PipyAdapters[AgentPort adapters]
    PipyNative[Native sessions]
    PipyProviders[ProviderPort providers]
    PipyTools[Bounded tool boundaries]
    PipyArchive[Metadata-first archive]
  end

  PiModes --> PiAgentSession --> PiAgent --> PiAI
  PiAgentSession --> PiTools
  PiAgentSession --> PiSessions
  PiAgentSession --> PiExt

  PipyCLI --> PipyRunner --> PipyAdapters --> PipyNative --> PipyProviders
  PipyNative --> PipyTools
  PipyRunner --> PipyArchive

  classDef pi fill:#fff7ed,stroke:#c2410c,color:#111111;
  classDef pipy fill:#eef2ff,stroke:#1d4ed8,color:#111111;
  class PiModes,PiAgentSession,PiAgent,PiAI,PiTools,PiSessions,PiExt pi;
  class PipyCLI,PipyRunner,PipyAdapters,PipyNative,PipyProviders,PipyTools,PipyArchive pipy;
```

Pi's durable center is `AgentSession`: it composes agent state, model and
thinking-level management, persistence, settings, resources, extensions, bash,
compaction, branching, and mode integration. Interactive, print, RPC, and SDK
surfaces sit above that shared session abstraction.

Pipy's durable center is currently split:

- `HarnessRunner` owns run lifecycle, event recording, and finalization.
- `NativeAgentSession` and `NativeNoToolReplSession` own native provider/tool
  control flow.
- `pipy_session.recorder` owns file lifecycle.
- `pipy_session.catalog` owns read-only archive inspection.

That split is deliberate. Pipy is using clean-architecture boundaries while it
bootstraps, so effectful adapters cannot silently become the product core.

## Key Design Differences

| Topic | Pi | Pipy |
| --- | --- | --- |
| Language and package shape | TypeScript monorepo with `coding-agent`, `agent`, `ai`, `tui`, and related packages. | Python package with `pipy_harness` and `pipy_session`. |
| Main runtime center | `AgentSession` wrapped around `pi-agent-core` and `pi-ai`. | `HarnessRunner` plus native session classes behind explicit ports. |
| UI | Rich TUI with editor, footer, selectors, overlays, and extension UI. | Line-oriented REPL with compact startup chrome and stderr diagnostics. |
| Session storage | Full tree JSONL sessions with parent links, branching, compaction, and resume workflows. | Immutable metadata-first JSONL plus Markdown summaries under `pipy/YYYY/MM`; no raw transcript import by default. |
| Tool model | Model-visible read, write, edit, and bash tools are core defaults. | Explicit, bounded, pipy-owned command/tool boundaries; no general model-selected tool loop yet. |
| Approval posture | No permission popups for the normal product workflow. | Same direction for explicit REPL read/context commands, while non-interactive request objects still carry policy and authority data. |
| Provider access | Broad provider/model registry through Pi's AI package, including subscription and API-key paths. | Four current providers behind `ProviderPort`: fake, OpenAI API-key, OpenAI Codex OAuth, and OpenRouter. |
| Extension system | First-class extensions, skills, prompt templates, themes, custom commands, and UI hooks. | Deferred. The current code has no extension runtime. |
| Privacy posture | Full Pi sessions are native product transcripts. | Pipy archive is metadata-first and excludes prompts, model output, provider payloads, file contents, command output, and auth material by default. |
| External agent wrapping | Pi is itself the product. | Pipy can wrap external CLIs for conservative capture, but external wrappers are not the product runtime. |
| Verification | Pi exposes broad bash/tool capability. | Pipy exposes only `/verify just-check` after successful same-session apply. |

Pi's README describes `read`, `write`, `edit`, and `bash` as the default model
tools. The Pi codebase also includes additional tool modules such as `find`,
`grep`, `ls`, `edit-diff`, and `truncate`; the table compares the default
product posture rather than every shipped helper.

## Pipy Layering Compared With Pi

Pi integrates more behavior inside its session and agent abstractions because it
already has a mature product surface. Pipy keeps sharper early boundaries:

- Domain value objects in `pipy_harness.native.models` define safe request,
  result, policy, and storage metadata.
- Provider adapters implement only `ProviderPort.complete()`.
- Tool boundaries implement explicit read, patch apply, and verification
  request shapes.
- The harness runner is the only layer that coordinates archive finalization.
- The catalog is read-only and never repairs, imports, or indexes raw records.

This means pipy currently has less product capability, but the code more
clearly separates:

- pure or mostly pure domain data,
- orchestration control flow,
- provider adapters,
- workspace effects,
- recorder/archive effects,
- and external subprocess capture.

## Compatibility Rules

Future Pi parity work should preserve these pipy-specific rules:

- `pipy-native` remains the product runtime.
- Codex, Claude, Pi, and arbitrary subprocess wrapping remain capture/reference
  paths unless the product direction explicitly changes.
- Raw prompts, model output, provider responses, stdout, stderr, command output,
  file contents, patches, diffs, secrets, credentials, tokens, private keys,
  and sensitive personal data stay out of archives by default.
- User-visible runtime behavior and docs must stay aligned in the same change.
- Broad features should land as small named boundaries, with focused tests,
  `just check`, docs updates, and review.

## Reading The Current Roadmap

Use these docs together:

- [Architecture](architecture.md) explains what exists now and where it lives.
- [Backlog](backlog.md) is the current slice index and historical ledger.
- [Harness Spec](harness-spec.md) records detailed rationale and deferred
  design.
- [Session Storage](session-storage.md) is the archive and privacy policy.
