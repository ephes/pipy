# Pipy Backlog

Status: planning index

This backlog records the current implementation direction at a task-slice
level. It is not a full issue tracker. Use it to choose the next small,
reviewable change while keeping the source-of-truth design constraints in
`docs/harness-spec.md` and `docs/session-storage.md`.

**The parity roadmap lives in [parity-plan.md](parity-plan.md).** That document
is the single clear plan for reaching real feature parity with Pi: the
slash-command and CLI matrices, the list of accidental pipy-only surfaces to
remove or realign (§3 there, mirrored in `Parity Cleanup` below), and the index
of per-topic specs with their conformance gates. The latest ranked comparison
snapshot against `/Users/jochen/src/pi-mono` is
[pi-mono-gap-audit.md](pi-mono-gap-audit.md). Read the plan first, then use the
audit for current slice selection. The big-topic specs it indexes are
[session-tree.md](session-tree.md),
[extension-api.md](extension-api.md), [provider-catalog.md](provider-catalog.md),
[settings-config.md](settings-config.md), [automation-rpc.md](automation-rpc.md),
[tui-workflow.md](tui-workflow.md), and
[export-distribution.md](export-distribution.md).

## Current State

Pipy is a native coding-agent runtime with a Pi-shape REPL, twelve stdlib-only
real providers plus the deterministic fake provider, a bounded model-driven
tool loop, and a metadata-first session archive. The first local model path is
`ds4` (`antirez/ds4` DeepSeek V4 Flash) through the OpenAI-compatible Chat
Completions machinery with tool-loop support. Specific feature coverage and
parity status live in
[pi-parity.md](pi-parity.md). Code shape lives in
[architecture.md](architecture.md).

This page is the forward-planning index:

- `Pi Parity Roadmap` is the broad ladder toward full Pi-class capability.
- The named parity tracks below record the work blocks that landed or are
  still in flight (Tool-Loop, OpenAI Responses + Codex Tool-Call, Workspace
  Context Loading, Streaming Output, and the 2026-05-26 Code Quality Audit
  Track CQ-A through CQ-F).
- `Next Slice` is the only current implementation target.
- `Near Term` explains why that slice is the next one.
- `Deferred` and `Explicitly Not Now` define boundaries that should not be
  crossed opportunistically.

## Pi Parity Roadmap

Pipy is a Python slopfork of Pi, so the long-term product target is Pi-class
native coding-agent capability — including the terminal UI — through
pipy-owned Python boundaries. Parity means matching the useful product
surfaces and workflows in pipy's architecture. It does not require a literal
port of Pi's TypeScript implementation, the `pi-tui` library, or exact command
names; it does require comparable end-user capability.

The broad parity ladder, applied with small-slice discipline:

- Shell chrome and orientation: startup header, safe loaded-resource labels,
  compact command affordances, and status/footer-style state presentation.
- Interactive input ergonomics: input-adapter boundary, slash-menu raw-mode
  line editor, optional prompt-toolkit line-editor adapter, stdlib readline
  fall-through, and plain captured-stream fallback. The product TUI now ships
  the core daily-driver editor ergonomics: in-memory Up/Down prompt history,
  ANSI bracketed paste (literal multi-line insert, no accidental submission),
  Ctrl-Z/Ctrl-Y undo/redo, and poll-based terminal resize handling that keeps
  the inline frame coherent at 80x24 and 100x40. Optional persistent
  cross-session prompt history now ships too (behind the `/settings` toggle,
  off by default, local-only state file). A fuller TUI is still on the ladder.
  The input-adapter boundary preserves plain captured-stream fallback.
  The stdlib-only `slash-menu` raw-mode line editor, stdlib `readline`
  adapter, and Workspace-relative path completion remain part of the
  input-parity ladder.
- Context/resource loading: safe AGENTS/CLAUDE-style instruction discovery
  with metadata-only archive behavior. Runtime resource loading for skills,
  prompt templates, and custom slash commands has now shipped (see the
  Runtime Resource Loading Track below): `/skill`, `/template`, and workspace
  custom `/<name>` commands run through `pipy_harness.native.resources` in
  both REPL product paths, and the `[Skills]` chrome section lists the
  loadable skill names. Chrome color themes also now ship via `/theme`
  (`pipy_harness.native.themes`). A general extension/package/plugin loader
  remains later parity work. The `/settings` (interactive control
  dialog) and `/model` (interactive selector) surfaces are exposed inside the
  product TUI, and `/login`/`/logout` are executable inside the TUI too
  (through the same auth boundary, with no provider turn).
- Tool parity: the bounded multi-step model/tool loop has landed; user-directed
  `@file` content injection has shipped (a submitted prompt's `@path`
  references load bounded excerpts through the shared bounded reader in both
  REPL modes); the model-visible `bash` tool has shipped as a real shell
  matching Pi (arbitrary commands in the workspace, combined bounded output,
  optional timeout, streamed live); the remaining gaps are
  tool breadth inside that loop, broader verification, and follow-up tool
  observations behind pipy-owned boundaries, explicit scopes, and privacy
  invariants.
- Session workflow parity: durable sessions, resume/search/inspect surfaces,
  compaction/summarization, branch/fork-style exploration, and review-cycle
  learning.
- Extension/RPC parity: the headless automation protocol has **shipped** —
  `--mode json` (full Pi-shaped event stream), `--print`/`-p` (one-shot text),
  and `--mode rpc` (long-lived stdin/stdout JSONL with the full Pi command
  vocabulary), gated by
  `scripts/parity_checks/automation_rpc_conformance.py --json`
  ([automation-rpc.md](automation-rpc.md)). Remaining integration points
  (extension APIs, custom commands/UI surfaced over the RPC extension-UI
  channel) build on this foundation.

### Prioritized Pi Gap Queue (2026-05-28)

This queue reflects the 2026-05-28 multi-agent comparison against the local Pi
reference plus the existing summary-safe parity history. It is a planning order,
not a promise to skip review when a smaller, safer slice appears.

1. Product-TUI reasoning italics. Implemented. `ChromeStyle.dim_italic`
   composes the italic SGR (`3`) with the existing secondary-dim color while
   respecting TTY/`NO_COLOR`/truecolor behavior, and `ToolLoopTerminalUi`
   renders `reasoning` rows with it so the product TUI matches Pi and pipy's
   captured-stream fallback renderer.
2. Product-TUI settings/model/provider controls. Interactive provider/model
   selection has landed in the product TUI alongside the existing read-only
   `/settings` overlay. `/model` opens a keyboard-navigable selector
   (`ToolLoopTerminalUi.run_model_selector`) built from
   `NativeReplProviderState.model_options()`: rows show the `provider/model`
   reference plus availability state and reasons, the active selection is marked
   `(current)`, Up/Down move the highlight (wrapping), Enter chooses an
   available row, and Esc cancels. Unavailable providers — and providers that do
   not advertise tool-call support, which tool-loop mode requires — stay visible
   with a reason but cannot be chosen. A successful choice switches through
   `NativeReplProviderState.select_model`, rebinds the live provider, clears the
   in-memory conversation context, refreshes the footer/status model label,
   persists the non-secret default, and constructs the next provider turn with
   the new provider/model; selection runs no provider turn. A direct
   `/model <provider>/<model>` form also switches (and works in the
   captured-stream fallback). The slash menu now lists `model`, `login`, and
   `logout` alongside `help`, `settings`, `copy`, `exit`, and `quit`. `/login
   [openai-codex]` and `/logout [openai-codex]` are now executable in the
   product TUI through the same `NativeReplProviderState` auth boundary the
   no-tool REPL uses: they run no provider turn and no tool call, clear the
   in-memory conversation, refresh `model_options()` availability, and rebind
   the live provider/footer (logout resets the selection to the local default).
   Interactive login output (the OAuth URL/prompt) renders only on the live
   terminal — the inline frame is suspended around it and repainted afterward —
   and never reaches the session archive. 2026-05-29 parity gap (now closed):
   `/settings` is an interactive in-frame control dialog
   (`ToolLoopTerminalUi.run_settings_dialog`) drawn in the live region, matching
   Pi's shape — highlighted actionable rows, scroll/windowing when the list
   overflows, bottom-key affordances (`↑/↓ move · enter/space act · esc close`),
   and Esc/Ctrl-C/Ctrl-D cancel. It exposes read-only status rows (active
   selection and per-provider availability) plus actionable rows: change
   provider/model (reuses the `/model` selector), openai-codex auth (reuses the
   `/login`/`/logout` boundary), toggle persistent prompt history, and clear
   persisted history. All actions run no provider turn and no tool call; the
   provider/model and auth actions reuse the existing `NativeReplProviderState`
   boundaries. Verified by real-PTY product-path tests at 80x24 and 100x40 that
   open the dialog, inspect the live overlay before any action, navigate/toggle/
   clear, resize while the dialog is open, and cancel back to the input.
3. Full interactive TUI ergonomics. The product TUI now renders inline (no
   alternate screen): finalized blocks commit once into the terminal's normal
   buffer so the host terminal/multiplexer keeps them in native scrollback, and
   only a small live region (bounded stream tail + separator/input/separator
   frame + slash menu + two footer rows) is redrawn in place, pinned at the
   bottom. This landed the three previously observed gaps: `/copy` is now an
   executable local command (safe OS clipboard / OSC 52, no provider turn);
   scrolling to review prior output works in both a native Ghostty terminal and
   a zellij pane because native scrollback is no longer suppressed; and a
   full-size window uses its full height (content fills down to the bottom-
   pinned input/footer) instead of only the upper half. Verified by real-PTY
   product-path tests at 100x40 and 80x24 (`tests/test_native_tool_loop_tui_pty.py`)
   plus tmux captures. In-app `/model` selection has since landed as an
   interactive keyboard-navigable selector (see Pi Gap Queue item 2). The core
   editor-ergonomics gap has since closed too: in-app `/login`/`/logout`,
   in-memory Up/Down prompt history, ANSI bracketed paste (literal multi-line
   insert with no accidental submission), Ctrl-Z/Ctrl-Y undo/redo, and
   poll-based resize/SIGWINCH handling now ship and are covered by real-PTY
   keystroke tests (history recall, paste, undo/redo, fake-auth login/logout
   without a provider turn, and TIOCSWINSZ resize at 80x24 and 100x40). Optional
   persistent cross-session prompt history has since landed too: behind the
   `/settings` "persistent prompt history" toggle (off by default), submitted
   prompts are saved to a local-only, capped, owner-private state file
   (`PromptHistoryStore`, `~/.local/state/pipy/prompt-history.json`, overridable
   via `PIPY_PROMPT_HISTORY_PATH`; never the metadata-first session archive) and
   a fresh session seeds Up/Down recall from them; "clear persisted history"
   wipes it. Covered by store unit tests and a cross-session real-PTY recall
   test. **The remaining editor-workflow gaps have since shipped** through the
   [tui-workflow.md](tui-workflow.md) track (gated by
   `scripts/parity_checks/tui_workflow_conformance.py`): the `@` file picker
   (exact/prefix/substring ranking) and Tab path completion, `!`/`!!` shell
   shortcuts, `Shift+Tab` thinking-level and `Ctrl+P`/`Shift+Ctrl+P` model
   cycling, `Ctrl+O`/`Ctrl+T` folding, queued steering/follow-up, `Ctrl+V`
   clipboard / drag image references, the `/scoped-models` + `/hotkeys` overlays
   and new `/settings` rows, and the mouse-selection invariant.
4. Extension and resource runtime. Pi has first-class extensions, command/theme
   registration, prompt templates, skills, and UI hooks. Runtime resource
   loading has now landed for the three bounded kinds — skills, prompt
   templates, and custom slash commands — through `pipy_harness.native.resources`,
   wired into both REPL product paths (see the Runtime Resource Loading Track
   below); the `[Skills]` chrome now lists loadable skill names from the real
   loader. A general extension/package loader, theme registration, and UI hooks
   remain deferred and are intentionally **not** built as part of that track.
5. Session workflows — **shipped (2026-06-02)**. The native product session
   tree (`pipy_harness.native.session_tree`) is now pipy's product session
   store, a raw private append-only JSONL tree like Pi's
   `~/.pi/agent/sessions/...` files. Product sessions persist raw
   user/assistant/tool history, rebuild provider context from the active
   branch, and expose `/session`, `/name`, `/new`, `/tree`, `/resume`
   (interactive picker overlay + non-TTY subcommands), `/fork`, `/clone`,
   durable `/compact`, the full Pi-style startup session flag set
   (`-c`/`-r`/`--session`/`--session-id`/`--session-dir`/`--name`/`--fork`/
   `--no-session`, mutual exclusion, cross-project fork prompt; the old
   `--resume`/`--branch` metadata flags retired), and branch summaries through
   pipy-owned boundaries. The existing metadata-first `pipy-session` archive
   stays a separate learning/catalog surface and is not the product session
   source. Design, behavior, and the passing conformance + Pi-comparison gates
   are in [session-tree.md](session-tree.md).
6. Tool breadth and project policy. The bounded multi-step loop is real and the
   model-visible `bash` tool is now a real shell matching Pi (arbitrary
   commands, combined bounded output, optional timeout, streamed live). The
   former pipy-specific `/verify just-check` REPL command has been removed; any
   future project-defined verification policy needs its own spec and should map
   to Pi's broad `bash`/extension-gate workflow rather than a Pi slash command.
7. User-directed context and attachments. Workspace instruction files are
   auto-loaded into the system prompt, repeated bounded file reads can load
   multiple text files across turns, and user-directed `@file` references in a
   submitted prompt now load bounded excerpts (multiple per turn, de-duped,
   fail-closed) into the next provider request in both REPL modes through the
   shared bounded reader. Still missing are pasted image/binary attachments,
   richer context pickers, and broader repo/resource maps.
8. Active-turn cancellation fidelity — **resolved**. Escape **and** Ctrl-C during
   an active turn now truly cancel the in-flight provider request: a per-turn
   `CancelToken` (`pipy_harness.native.cancellation`) is threaded through
   `ProviderPort.complete(...)` into the `urllib`/SSE HTTP boundary. The
   underlying connection registers on the token at `connect()` time, so on abort
   `_complete_provider_turn` shuts the socket down — interrupting both the
   header wait (non-streaming JSON blocks inside `urlopen()` until generation
   finishes) and any body/stream read — so the worker's blocking read raises
   `ProviderCancelledError`. It then best-effort joins the daemon worker and
   renders red `Operation aborted`, returning to a usable prompt. Cancellation
   is cooperative: the aborted turn returns without appending an assistant/tool
   observation and late chunks are suppressed, so even a provider that ignored
   the token cannot mutate session/context state. The socket-shutdown read path
   tolerates CPython's `http.client._close_conn` shutdown race — a concurrent
   `self.fp = None` can surface as `AttributeError: 'NoneType' object has no
   attribute 'close'` rather than `OSError` — by mapping that (plus
   `OSError`/`ValueError`/`HTTPException`) to `ProviderCancelledError` only when
   the token is cancelled, so an aborted body read never leaks a spurious
   provider error while a genuine non-cancel error still propagates. Proven by
   boundary unit tests (real-socket proofs for the header wait, a
   `Content-Length` body read, and a `Connection: close` body read; an SSE
   EOF-on-cancel guard; deterministic `AttributeError`-mapping proofs in both the
   cancelled and not-cancelled directions) and a real-PTY test that drives the
   actual Escape/Ctrl-C key sequences mid-turn.
9. Multi-agent orchestration, indexing, and local-provider maturity. The
   headless RPC/JSON automation protocol has shipped (item 5 above). The local
   ds4 provider now has real large-model one-shot and tool-call smoke coverage;
   remaining product maturity work is broader local-provider benchmarking after
   the core shell, tool, session, and settings surfaces settle.

Textual, prompt-toolkit, curses, and a small custom terminal layer were
compared at the terminal-layer checkpoint. The current direction is a narrow
`prompt-toolkit` line-editor adapter as the first step. Textual, curses, and a
custom terminal layer stay on the table for when the product needs a fuller UI
surface or lower-level terminal ownership.

### Current Largest Pi Feature Gaps (groomed 2026-06-15)

This snapshot supersedes the 2026-06-03 ranking after the session tree,
session CLI/pickers, product TUI workflow, settings/keybindings, RPC/JSON
automation, provider-catalog construction, and active-turn cancellation all
shipped. It is a slice-selection aid, not a replacement for
`docs/pi-parity.md` or the per-topic specs.

Shipped foundations that should no longer be selected as large topics:

- native product session tree, `/session`/`/name`/`/new`/`/tree`/`/resume`/
  `/fork`/`/clone`, durable `/compact`, and the full Pi startup-session flag
  set;
- product TUI/editor workflow depth, including `@` picker, path completion,
  `!`/`!!`, thinking/model hotkeys, folding, queued steering/follow-up,
  clipboard/drag image references, overlays, mouse-selection invariant, and
  true provider-request cancellation;
- layered settings/keybindings, scoped models, resource toggles, `/reload`,
  `/changelog`, and `--version`;
- Pi-shaped `--mode json`, `--print`/`-p`, and `--mode rpc`; and
- provider/model catalog construction for the implemented adapter families,
  one-shot runs, and startup resolution.

The highest-impact remaining gaps are now:

1. **Extension and package platform — selected next big topic.** Pi's most
   important remaining differentiator is its extension/package surface:
   trusted local code can register model-visible tools, commands, providers,
   keybindings, lifecycle hooks, UI surfaces, and package resources. Pipy
   currently has bounded Markdown resources (`.pipy/skills`,
   `.pipy/templates`, `.pipy/commands`) and chrome themes, but no general
   Python extension runtime or package manager. The next implementation slice
   should be the safest foundation: local Python extension discovery and
   manifest inventory with fail-closed diagnostics and **no extension code
   execution yet**. The target API and later slices live in
   [extension-api.md](extension-api.md).
2. **Export / import / share / distribution.** The native session tree now
   contains full product transcripts, so pipy can add Pi-style full-session
   HTML export, active-branch JSONL export, import-and-resume, private gist
   share, `--export`, and install/update documentation. This is more bounded
   than extensions and is the best alternate next topic if implementation risk
   should be lower. Spec: [export-distribution.md](export-distribution.md).
3. **User documentation parity.** Pipy still has mostly maintainer/agent specs
   rather than Pi-like product docs for quickstart, usage, providers, settings,
   keybindings, sessions, customization, automation, SDK/RPC, terminal setup,
   tmux, and platforms. This can run in parallel with implementation tracks.
   Spec: [user-documentation.md](user-documentation.md).
4. **Provider/model catalog follow-ons.** Remaining provider work is narrower
   adapter/product polish: live Anthropic/Copilot login UX, Vertex API-key auth,
   Anthropic adaptive-thinking shape, Azure URL/api-version parity, the
   deliberate `openai-codex-responses` legacy-factory exception for
   settings-derived retry policy, broader local-provider benchmarking, and
   extension-registered providers after the extension API exists. Spec:
   [provider-catalog.md](provider-catalog.md).
5. **Top-level CLI compatibility and parity cleanup.** Pipy still exposes a
   harness-shaped `auth|run|repl` layout in places where Pi has a single
   product command, and several pipy-only surfaces remain to remove or realign
   (`--archive-transcript`, no-tool REPL/proposal commands, `/clear`,
   `/status`, `/theme`, `/skill`, `/template`, `/help`, and exposed internal
   flags where they do not map to Pi). This cleanup should be staged alongside
   the topic that owns each surface rather than done as one risky rewrite.
6. **Verification breadth and policy.** Pipy now relies on the model-visible
   `bash` tool for Pi-style verification-like workflows. Richer project policy
   should come through extension-defined permission/tool gates once the
   extension platform exists, not through a revived pipy-only `/verify` command.

## Parity Cleanup: accidental pipy-only surfaces to remove or realign

These surfaces exist only in pipy and not in Pi. Per the parity principle
(`parity-plan.md` §1), a pipy-only surface is removed or realigned to Pi unless
there is a genuinely good reason to keep it — **privacy and security are not good
reasons.** Pi stores full session transcripts, streams full session events, and
exports full sessions; pipy's "metadata-first" posture is a pipy preference, not
a parity virtue, and must not justify diverging from Pi. The full table with
rationale is in [parity-plan.md](parity-plan.md) §3; the actionable removals are:

- **Metadata-first `pipy-session` archive as the product session store.**
  **Resolved (2026-06-02).** The full-transcript native session tree
  ([session-tree.md](session-tree.md)) is now the product store; `pipy-session`
  is a separate, non-default metadata catalog/learning utility. The docs/specs
  no longer present metadata-first as the product session source.
- **`--archive-transcript` sidecar.** Retire once the native session tree stores
  full transcripts like Pi. The native tree *is* the transcript.
- **`--native-output json` (metadata-only).** **Deprecated** in favor of Pi's
  `--mode json` full-event stream and `--mode rpc`, which have shipped
  ([automation-rpc.md](automation-rpc.md)); its `--help` now points there. The
  metadata-only object is retained on `pipy run` for existing callers and is
  scheduled for removal once no caller depends on it.
- **No-tool REPL mode and its `/read` `/ask-file` `/propose-file`
  `/apply-proposal` commands.** Pi has one interactive mode with model-visible
  tools. Fold into the single tool-loop product session and remove the
  proposal/apply commands plus the archive-side parallel tool family that backs
  them (Track CQ-A slice 10, Track CQ-D slice 1).
- **`/clear` → `/new` + `/compact`; `/status` → `/session`.** Realign the
  pipy-only commands to Pi's command set ([session-tree.md](session-tree.md)).
- **`/theme` command, `/skill`/`/template` dispatcher commands, `/help`.** Pi has
  none of these. Move theme selection under `/settings` (keep `--theme`),
  auto-inject skills, and register prompt templates as their own `/<name>` slash
  commands. (`/hotkeys` now ships, rendered from the resolved keybinding
  manager.) ([settings-config.md](settings-config.md),
  [provider-catalog.md](provider-catalog.md)).
- **Hardcoded `ds4` built-in provider.** Reframe as a `models.json`
  custom-provider preset ([provider-catalog.md](provider-catalog.md)).
- **Verify-and-decide:** `--read-root(s)`, `--tool-budget`, `--input-runtime`,
  and persistent prompt history (`PromptHistoryStore`) are pipy-only mechanisms
  with possible non-privacy justifications; keep only if they map to a real Pi
  workflow or are cheap, clearly-useful conveniences, otherwise drop.
- **Keep (non-feature):** the CQ-A..F code-quality audit tracks are pipy
  engineering hygiene, not Pi features, and stay as internal cleanup work.
- **Already done:** the `/verify just-check` command was removed (not a Pi
  feature).

`docs/parity-criterion.md`, `docs/pi-parity.md`, and `docs/session-storage.md`
have been updated so they no longer present the metadata-first/privacy posture as
a parity virtue or a reason to diverge from Pi.

## Tool-Loop Parity Track

The bounded model-selected tool loop behind
`pipy repl --agent pipy-native --repl-mode tool-loop` is now implemented.
It shipped as twelve reviewed slices plus an OpenRouter response-parser
follow-up and a first-review fix-up commit, all alongside the existing
no-tool REPL and the existing slash-command boundaries. Each slice
landed as a named conventional commit with focused tests, `just check`,
updated docs, and a stop for review. OpenRouter was the first real
provider with `supports_tool_calls=True`; OpenAI Responses and OpenAI
Codex parsers now ship through the separate
[OpenAI Responses + OpenAI Codex Tool-Call Parity Track](#openai-responses-openai-codex-tool-call-parity-track).

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Native Tool-Loop Parity Track`) and the parity-map
entry in `docs/pi-parity.md` (`Native Tool-Loop Parity Track`).

### Goal

- A real model-driven loop over `openai`, `openai-codex`, and `openrouter` with
  bounded `read`, `write`, `edit`, `ls`, `grep`, and `find` tools, producing a
  useful end-to-end change against this repo with `just check` green.
- Pi-shaped behavior: the model picks files, edits them directly, the resulting
  unified diff is written to stderr, no approval popups appear, and the loop
  iterates within a bounded tool budget.
- Slash commands `/read`, `/ask-file`, `/propose-file`, and
  `/apply-proposal` keep working unchanged in `--repl-mode no-tool`; the former
  pipy-specific `/verify just-check` command has been removed from the user-facing REPL.

### Planned Slices

1. Docs only. Record the tool-loop parity goal, invariants, and deferred work
   in `docs/pi-parity.md`, `docs/backlog.md`, and `docs/harness-spec.md`.
2. `tools/base.py` contracts: `ToolDefinition`, `ToolRequest`,
   `ToolExecutionResult`, `ToolArgumentError`, `ToolContext`, and `ToolPort`,
   built from stdlib dataclasses with manual JSON-schema validation. Focused
   contract tests, no provider or REPL wiring.
3. `ProviderPort` extension: a `supports_tool_calls` capability flag (real
   providers stay `False`), a `ProviderToolCall` value object, `tool_calls` on
   `ProviderResult`, and a provider-agnostic message envelope
   (`user`/`assistant`/`tool_result`). The fake provider gains
   `programmable_tool_calls` for tests; real adapters stay inert.
4. `NativeToolReplSession` skeleton: bounded turn loop with `--tool-budget`
   defaulting to 10 (max 25), malformed tool arguments returned to the model as
   an observation (fatal after three consecutive malformed turns), a test-only
   `_FixtureTool` injected by tests, and an empty production tool registry.
5. `read` tool: reuses `read_only_tool.py` validation. The first real provider
   adapter flips `supports_tool_calls` to `True`; a manual smoke run lands with
   the slice.
6. `ls` tool: bounded directory entries returned as workspace-relative paths.
7. `grep` tool: `subprocess.run` to `rg` with no `shell=True`, a fixed argv, a
   workspace `cwd`, a timeout, and bounded results, with a stdlib fallback when
   `rg` is unavailable.
8. `find` tool: bounded glob lookup.
9. `write` tool: create-only; refuses existing files, `.git`, and paths that
   escape the workspace; applies directly and writes the unified diff to
   stderr. Tests pin: file mutation, diff lands only on stderr, archive remains
   untouched, and the diff lands in the opt-in sidecar only when enabled.
10. `edit` tool: string-replace with a unique-`old_string` default and an
    opt-in `replace_all`; reuses `patch_apply.py`. Same diff and archive
    privacy tests.
11. Opt-in `TranscriptSink`: a sidecar JSONL at
    `~/.local/state/pipy/transcripts/<id>.jsonl`, enabled by
    `--archive-transcript`, marked sensitive, written outside the metadata
    archive, and excluded from `pipy-session list/search/inspect`. Focused
    privacy tests.
12. Flip the default `--repl-mode` to `tool-loop` when the selected provider
    supports tool calls. The `no-tool` mode stays available. Update README and
    user-facing docs.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly across the whole track.
  `pipy_session.recorder` records no prompts, model text, tool payloads, file
  contents, or diffs in any slice. Any leak fails the slice.
- `.git` is default-deny across all model-driven tools. Slash commands are
  unaffected.
- No new runtime dependencies. Stdlib plus manual dict validation only; no
  pydantic.
- `NativeToolResult` carries archive-safe metadata only;
  `ToolExecutionResult` carries provider-visible payloads. The two shapes are
  not conflated.
- The internal pipy-owned `tool_request_id` does not leak as a provider id;
  provider identifiers are carried separately as `provider_correlation_id`.
- The existing no-tool REPL and the listed slash commands keep working in both
  modes.
- Each slice ships focused tests, a green `just check`, updated docs, a
  conventional commit, and stops for review.

### Out Of Scope For This Track

These were explicitly deferred for the original tool-loop track; some have now
shipped in later parity work:

- Arbitrary shell execution. **Update (shipped):** the model-visible `bash`
  tool is now a real shell, matching Pi's bash tool — it spawns `bash -c
  <command>` in the workspace root with the inherited environment, an optional
  timeout (the process group is killed when it elapses), and returns combined,
  bounded stdout/stderr to the model. Pipes, redirection, substitution,
  globbing, chaining, and any executable on `PATH` are allowed. Only metadata
  (counters, labels) is recorded at the archive boundary — never the raw
  command or output.
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow.
- Live session resume, branch/fork navigation, and compaction. A metadata-only
  resume reader shipped first; live `--resume`, `--branch`, and `/compact`
  (plus an automatic compaction threshold) shipped later through the Native
  Session Workflow Track below.
- RPC mode and SDK embedding. The in-process Python SDK shipped, and the
  headless `--mode json`/`--mode rpc`/`--print` automation protocol has now
  shipped too ([automation-rpc.md](automation-rpc.md)); only the
  network/socket daemon remains deferred.
- Extensions, package loading, theme integration, and slash-command loading for
  skills and prompt templates. A pure theme registry shipped later.
- ~~Automatic `@file` content reads from completion-only references.~~
  (Historical: was out of scope for this track. User-directed `@file` content
  reads subsequently shipped — a submitted prompt's `@path` references load
  bounded excerpts through the shared bounded reader in both REPL modes.)
- Persistent shell history and a full interactive TUI.
- Additional providers beyond `openai`, `openai-codex`, and `openrouter`.
  Shipped later for the eight providers listed in `docs/parity-criterion.md`.
- Removing the no-tool REPL or its slash-command boundaries.

## OpenAI Responses + OpenAI Codex Tool-Call Parity Track

The Native Tool-Loop Parity Track originally shipped end-to-end with
OpenRouter as the only real provider advertising
`supports_tool_calls=True`. This follow-up track extended the same
loop closure to `OpenAIResponsesProvider` and
`OpenAICodexResponsesProvider`, so `pipy repl --agent pipy-native
--native-provider openai` and `--native-provider openai-codex` now
drive the existing bounded tool loop end-to-end against their
respective endpoints, matching the bar set by OpenRouter in
`tests/test_tool_loop_end_to_end.py`,
`tests/test_tool_loop_end_to_end_openai.py`, and
`tests/test_tool_loop_end_to_end_openai_codex.py`.

Use this section together with the matching design notes in
`docs/harness-spec.md` (`OpenAI Responses + OpenAI Codex Tool-Call
Parity Track`) and the parity-map entry in `docs/pi-parity.md`
(`OpenAI Responses + OpenAI Codex Tool-Call Parity Track`).

### Goal

- `OpenAIResponsesProvider` serializes the provider-agnostic message
  envelope plus `available_tools` into the OpenAI Responses API
  `input`/`tools` shape, parses `function_call` outputs into
  `ProviderToolCall` values on `ProviderResult.tool_calls`, serializes
  `ToolResultMessage` as Responses `function_call_output` items, and
  flips `supports_tool_calls=True`.
- `OpenAICodexResponsesProvider` does the same over Codex Responses
  streaming, assembling function calls across the SSE event stream
  (`response.output_item.added` / `response.function_call_arguments.delta`
  / `response.output_item.done` or equivalents) and flipping
  `supports_tool_calls=True`.
- Each provider ships a hermetic end-to-end loop-closure test against a
  stub transport (JSON for `openai`, SSE for `openai-codex`), mirroring
  the OpenRouter bar in `tests/test_tool_loop_end_to_end.py`.
- Legacy no-tool / single-turn callers (`/ask-file`, `/propose-file`,
  `pipy run --agent pipy-native --goal ...`) keep their existing
  behavior; their tests stay green unchanged.

### Planned Slices

1. Docs only. Record the OpenAI parity goal, invariants, slice plan,
   and deferred work in `docs/pi-parity.md`, `docs/backlog.md`,
   `docs/harness-spec.md`, `docs/architecture.md`, and `README.md`.
2. `OpenAIResponsesProvider` tool-call wiring: serialize messages and
   tools into the Responses `input`/`tools` shape, parse `function_call`
   outputs into `ProviderToolCall`, serialize `ToolResultMessage` as
   `function_call_output`, flip `supports_tool_calls=True`, ship the
   hermetic JSON-transport end-to-end test, and update the existing
   `test_real_providers_advertise_tool_call_support_correctly`
   assertion that pins `openai.supports_tool_calls is False`.
3. `OpenAICodexResponsesProvider` tool-call wiring: serialize messages
   and tools into the Codex Responses streaming shape, assemble
   function calls across the SSE event stream, allow terminal
   `response.completed` without final text when tool_calls are present,
   serialize `ToolResultMessage` as `function_call_output`, flip
   `supports_tool_calls=True`, and ship the hermetic SSE-transport
   end-to-end test.
4. README and cross-doc cleanup: remove any remaining "follow-up" /
   "OpenRouter is the only" phrasing once both providers have shipped.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly. `pipy_session.recorder`
  records no prompts, model text, tool payloads, file contents, or diffs
  in any slice. Pinned by tests.
- `.git` is default-deny across all model-driven tools, including the
  resolved-symlink check via `_resolved_relative_label`.
- No new runtime dependencies. Stdlib plus manual dict validation only.
  No pydantic, jsonschema, or attrs.
- Reuse the existing tool-loop contracts and helpers (`ToolDefinition`,
  `ToolRequest`, `ToolExecutionResult`, `ToolPort`, `validate_arguments`,
  the `LoopMessage` envelope, `NativeToolReplSession`). Do not redesign
  the loop.
- `NativeToolResult` (archive-safe metadata) and `ToolExecutionResult`
  (provider-visible payload) stay strictly separate; do not conflate.
- Pipy-owned `tool_request_id` (`pipy-tool-` prefix) stays internal;
  provider identifiers ride separately as `provider_correlation_id`.
- The no-tool REPL and the existing slash commands keep working in
  both modes.
- The opt-in `--archive-transcript` sidecar contracts (path, exclusion
  from `pipy-session list/search/inspect`, off-by-default) are unchanged.
- Each slice ships focused tests, a green `just check`, updated docs,
  a conventional commit, and stops for review.

### Out Of Scope For This Track

- Project-defined verification policy beyond the Pi-style `bash` workflow, session
  resume/branch/compaction, RPC mode, SDK embedding, extensions,
  theme/package loading, persistent history, and a full TUI. (User-directed
  `@file` content reads were once out of scope here and have since shipped.)
- Removing the no-tool REPL or redesigning the tool-loop contracts.

## Workspace Context Loading Parity Track

The named Pi-parity track after the
[OpenAI Responses + OpenAI Codex Tool-Call Parity Track](#openai-responses-openai-codex-tool-call-parity-track)
added AGENTS.md / CLAUDE.md discovery and injection into the native
pipy system prompt and has now shipped end-to-end across the
one-shot runner, the `--repl-mode no-tool` REPL, and the
`--repl-mode tool-loop` REPL for `openai`, `openai-codex`, and
`openrouter`. The track landed as four reviewed conventional
commits (docs-only opener, pure loader plus unit tests,
system-prompt wiring plus round-trip tests, docs cleanup and close)
with focused tests, `just check`, updated docs, and a stop for
review at each slice. Pi's `loadProjectContextFiles` in
`pi-mono/packages/coding-agent/src/core/resource-loader.ts` resolves
its global agent configuration root, walks from the workspace upward
through every parent directory, picks the first existing file per
directory in the candidate list `AGENTS.md > AGENTS.MD > CLAUDE.md >
CLAUDE.MD`, and dedupes by canonical path; the returned list is
composed global-first, then ancestors from the root-most ancestor
down to the workspace's direct parent, then the workspace itself
last, so more-specific instructions override earlier ones. Pipy
slopforks the same behavior through pipy-owned Python boundaries in
`pipy_harness.native.workspace_context`, not as a literal
TypeScript port.

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Workspace Context Loading Parity Track`)
and the parity-map entry in `docs/pi-parity.md`
(`Workspace Context Loading Parity Track`).

### Goal

- `pipy repl --agent pipy-native` and `pipy run --agent pipy-native`
  send a system prompt that includes the workspace's `AGENTS.md` /
  `CLAUDE.md` content plus any parent-walk and global instructions,
  in both `--repl-mode tool-loop` and `--repl-mode no-tool`, across
  `openai`, `openai-codex`, and `openrouter`.
- A round-trip smoke shows the model honoring an instruction stated
  only in `AGENTS.md` and a hermetic test pins the same against the
  fake provider.
- Discovery rules are pinned by focused unit tests: per-directory
  candidate filename precedence, nested-workspace ordering, missing
  files do not fail, symlinks must resolve inside the directory where
  they are found, the global root respects `PIPY_CONFIG_HOME`, then
  `XDG_CONFIG_HOME/pipy`, then `~/.config/pipy`, and bounded
  per-file and total byte caps apply with deterministic truncation
  labels.
- `pipy-session` records only metadata about which instruction files
  were loaded: workspace-relative path or `<global>` label, sha256,
  byte length, and a `truncated` flag, plus a
  `total_byte_cap_reached` boolean. A test pins that no instruction
  body reaches session JSONL, the Markdown summary, or the opt-in
  `--archive-transcript` sidecar.

### Planned Slices

1. Docs only. Record the workspace-context parity goal, invariants,
   slice plan, and deferred work in `docs/pi-parity.md`,
   `docs/backlog.md`, and `docs/harness-spec.md`.
2. Workspace instruction loader. Add
   `pipy_harness.native.workspace_context` with a
   `WorkspaceInstructionFile` value object and a
   `discover_workspace_instructions(...)` helper that mirrors
   `loadProjectContextFiles` through pipy-owned Python: per-directory
   candidate precedence, parent-walk ordering (root-most ancestor
   first, the workspace itself last), the global root resolved
   through `PIPY_CONFIG_HOME` then `XDG_CONFIG_HOME/pipy` then
   `~/.config/pipy`, deduplication by canonical absolute path,
   symlink resolution that stays inside the containing directory, bounded
   per-file and total byte caps with deterministic truncation
   labels, and "missing files do not fail" semantics. Focused unit
   tests pin every rule. No REPL or run wiring in this slice.
3. System-prompt wiring and archive metadata. Compose the system
   prompt from the existing bootstrap base plus the discovered
   instructions, and pass it through `PipyNativeAdapter` (one-shot),
   `PipyNativeReplAdapter` (no-tool REPL), and
   `PipyNativeToolReplAdapter` (tool-loop). Record per-run
   `workspace_instruction_files` metadata (workspace-relative or
   `<global>` label, sha256, byte length) in the session safe
   context. Pin that bodies never appear in JSONL, the Markdown
   summary, or the `--archive-transcript` sidecar. Ship a hermetic
   round-trip test against a request-capturing fake provider that
   proves an `AGENTS.md` instruction reaches
   `ProviderRequest.system_prompt` across both REPL modes and the
   one-shot runner.
4. Docs cleanup and close. Move the parity-map row to "Implemented",
   remove the "Still To Slopfork" / "Deferred" wording for
   AGENTS/CLAUDE-style context discovery, refresh the Pi Parity
   Roadmap context/resource-loading bullet, and run a real-provider
   smoke (recorded as a metadata-only `pipy-session`) that honors an
   `AGENTS.md`-only instruction end-to-end.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly.
  `pipy_session.recorder` records no instruction bodies in any
  slice; only safe per-file metadata (path label, sha256, byte
  length). Pinned by tests.
- The opt-in `--archive-transcript` sidecar contracts (path,
  exclusion from `pipy-session list/search/inspect`, off-by-default)
  stay unchanged. Instruction bodies never reach the sidecar.
- `.git` default-deny posture and existing slash commands (`/read`,
  `/ask-file`, `/propose-file`, `/apply-proposal`) keep working unchanged in both REPL modes.
- No new runtime dependencies. Stdlib plus manual dict validation
  only. No pydantic, jsonschema, or attrs.
- Reuse the existing `ProviderPort` message envelope and
  `NativeToolReplSession`. Do not redesign the loop.
- Per-file and total byte caps are enforced before the prompt is
  composed; an over-cap file is included up to its slice with a
  deterministic truncation marker, and over-total reads stop at the
  total cap with a deterministic notice.
- Symlinks that resolve outside the containing directory are skipped; their
  metadata is not recorded.
- Each slice ships focused tests, a green `just check`, updated
  docs, a conventional commit, and stops for review.

### Out Of Scope For This Track

These remain explicitly deferred while the track lands and after
it lands. They are not later slices of this track:

- Slash-command loading for skills and prompt templates, extensions, and
  package loading. (Resolved later: runtime `/skill`, `/template`, and custom
  `/<name>` loading shipped in the Runtime Resource Loading Track below.
  General extensions, package loading, and a theme registry remain deferred.)
- Live session resume, branch/fork, compaction, and share. A metadata-only
  resume reader shipped first; live `--resume`, `--branch`, and `/compact`
  (with an automatic threshold) have since shipped through the Native Session
  Workflow Track below.
- Full TUI and persistent cross-session history. (Resize handling, in-memory
  prompt history, bracketed paste, undo/redo, an interactive `/settings` control
  dialog, and optional persistent cross-session prompt history have since shipped
  in the product TUI.)
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow.
- Watching the workspace for instruction-file changes during a
  session. The current track resolves instructions once per run.

## Streaming Output Parity Track

The named Pi-parity track after the
[Workspace Context Loading Parity Track](#workspace-context-loading-parity-track)
closes parity-criterion row C14 ("streaming output
(provider→stdout)"). Pi exposes provider chunks through
`AssistantMessageEventStream` in
`pi-mono/packages/ai/src/utils/event-stream.ts`; pipy slopforks the
useful surface — incremental text deltas reaching a configurable sink
during `pipy run` — through pipy-owned Python boundaries, not as a
literal event-stream port. The track ships as four reviewed slices
(docs-only opener, `ProviderPort` stream sink plus fake-provider
wiring, first real-provider streaming on `OpenAICodexResponsesProvider`,
and `pipy run --stream` plumbing plus the matching docs flip).

Use this section together with the matching design notes in
`docs/harness-spec.md` (`Streaming Output Parity Track`) and the
parity-map entry in `docs/pi-parity.md`
(`Streaming Output Parity Track`).

### Goal

- `pipy run --agent pipy-native --stream` routes provider-emitted
  text deltas to a configurable chunk sink (stdout in plain text
  mode, stderr in `--native-output json` mode) as they arrive, while
  the final successful provider text and the metadata-first archive
  records are unchanged.
- One real provider (`openai-codex`, whose SSE parser already
  iterates `response.output_text.delta` events) flips on streaming
  first. Other tool-capable providers (`openai`, `openrouter`) stay
  non-streaming for this track and remain functional on the existing
  buffered path.
- A hermetic streaming-stub test pins the chunk order and proves the
  same final `ProviderResult` shape whether streaming is enabled or
  not. The fake provider gains a `programmable_text_chunks` field
  for unit-level coverage that does not depend on transport details.
- `pipy run` without `--stream`, the no-tool REPL, the tool-loop
  REPL, `/ask-file`, `/propose-file`, `/apply-proposal`, and
  the no-tool REPL does not force any path through streaming
  through streaming.

### Planned Slices

1. Docs only. Record the streaming parity goal, invariants, slice
   plan, and deferred work in `docs/pi-parity.md`, `docs/backlog.md`,
   and `docs/harness-spec.md`.
2. `ProviderPort` stream sink. Add a `StreamChunkSink` callable
   alias in `pipy_harness.native.provider`, extend `complete(...)`
   with an optional keyword-only `stream_sink` parameter that
   defaults to `None`, and let `FakeNativeProvider` push a new
   `programmable_text_chunks` tuple through the sink when supplied.
   Existing real-provider implementations accept the keyword and
   ignore it; their existing buffered behavior is unchanged. Focused
   contract tests pin: missing sink keeps current behavior
   bit-for-bit; supplied sink receives chunks in order; the final
   `ProviderResult.final_text` is the concatenation of the supplied
   chunks.
3. First real-provider streaming. Wire
   `OpenAICodexResponsesProvider` to call the supplied sink for each
   parsed `response.output_text.delta` event before returning the
   buffered final text. A hermetic SSE-transport test injects a
   multi-delta stream and asserts: chunks reach the sink in source
   order, the final `ProviderResult.final_text` is byte-equivalent
   to the non-streaming case, and the session archive records no
   chunk bodies.
4. `pipy run --stream` plumbing plus C14 close. Add the `--stream`
   flag to `pipy run` (default off), route chunks to stdout in text
   mode and stderr in JSON mode, fail closed with a metadata-only
   stderr diagnostic when the active provider does not advertise
   streaming, flip parity-criterion C14 to `✅`, refresh
   `docs/pi-parity.md` (this section moves to the "What Has Been
   Slopforked" table), and re-run `just parity-score`.

### Invariants

These hold throughout the track, not as later deferrals:

- Metadata-first archive privacy is preserved exactly.
  `pipy_session.recorder` records no streamed chunk bodies, deltas,
  prompts, model text, tool payloads, file contents, or diffs in
  any slice. Pinned by tests.
- The opt-in `--archive-transcript` sidecar contracts (path,
  filename-safe id, regular owner-only file, symlink refusal,
  exclusion from `pipy-session list/search/inspect`, off-by-default)
  stay unchanged. Streamed chunks never reach the sidecar.
- `.git` default-deny posture and existing slash commands (`/read`,
  `/ask-file`, `/propose-file`, `/apply-proposal`) keep working unchanged in both REPL modes.
- No new runtime dependencies. Stdlib plus manual dict validation
  only. No pydantic, jsonschema, attrs, anyio, or trio.
- Reuse the existing `ProviderPort`, `ProviderRequest`, and
  `ProviderResult` shapes. The streaming surface is an optional
  keyword on `complete(...)`, not a new method or a new request
  envelope.
- Streaming is purely additive: a provider that does not implement
  the keyword keeps working, a caller that does not supply a sink
  keeps working, and `pipy run` without `--stream` keeps the
  existing default-text stdout contract.
- The internal pipy-owned `tool_request_id` and
  `provider_correlation_id` boundaries are unaffected; streaming
  carries no tool-call payloads in this track.
- Each slice ships focused tests, a green `just check`, updated
  docs, a conventional commit, and stops for review.

### Out Of Scope For This Track

These remain explicitly deferred while the track lands and after
it lands. They are not later slices of this track:

- Streaming tool-call argument deltas; tool calls remain buffered.
- Streaming thinking/reasoning deltas. Pipy stays metadata-only on
  thinking content.
- Streaming in `--repl-mode no-tool` and `--repl-mode tool-loop`;
  the initial track wires `pipy run` only.
- Streaming for providers other than `openai-codex`; the other
  eleven adapters stay on their buffered paths in this track.
- Image, binary, or multimodal chunks; the sink carries text only.
- Cancellation, backpressure, and async streaming. The sink is a
  synchronous callable invoked from the provider's existing
  thread/transport.

## Code Quality Audit Track (2026-05-26)

A seven-agent comparative audit ran against `pi-mono` on 2026-05-26 with the
brief: find AI slop, plausible-but-wrong control flow, permissive error
handling, and bad-state-handled-instead-of-prevented patterns that have
accreted in the pipy slopfork. The audits live under
`docs/audit/2026-05-26/code-quality-audit/` (151 findings, line-cited):

- `01-session-repl.md` — native session + REPL (20 findings, 4 high)
- `02-providers.md` — 12 provider adapters (20 findings, 5 high)
- `03-tools.md` — tools layer + dual `ToolPort` (24 findings, 6 high)
- `04-cli-runner.md` — CLI, runner, adapters (22 findings, 3 high)
- `05-session-storage.md` — `pipy_session` recorder/catalog (20 findings, 5 high)
- `06-chrome-resources.md` — chrome + resource discovery (23 findings, 7 high)
- `07-value-objects.md` — value objects + state (24 findings, 5 high)

The dominant signal: pipy ships ~29 KLoC against pi-mono's ~28 KLoC for
the equivalent feature surface (excluding pi-mono's `tui/`, `ai/` model
registry, and most providers), but at least 4–6 KLoC of that is
demonstrable slop: dead modules with zero production callers, parallel
families with overlapping responsibilities, eleven-fold duplication of
the same provider scaffolding, and defensive runtime guards on closed
type universes.

This track is not a single linear roadmap; it is a list of small,
reviewable cleanup slices grouped into six themed tracks. Each slice is
intended to ship as a focused conventional commit with `just check`
green and documentation updates. Pick the next slice from whichever
track has the highest leverage at the time. Audit file references are
shorthand for the detailed finding (e.g. `01:F3` is finding F3 in
`01-session-repl.md`).

### Invariants (apply across all tracks)

- No new runtime dependencies. Stdlib plus manual dict validation only.
  No pydantic, jsonschema, attrs, or typebox port.
- Metadata-first archive privacy is preserved exactly. No prompt, model
  text, tool payload, file content, or diff reaches JSONL, Markdown, or
  the catalog. Each cleanup slice that touches the boundary re-pins the
  invariant.
- `.git` default-deny, symlink containment, and bounded byte caps
  remain in force across every tool and resource loader.
- The bounded model-driven tool loop, `--archive-transcript` sidecar
  contracts, `/login`/`/logout`/`/model`/`/clear`/`/status` slash
  commands, and the public read/apply boundary all keep working
  in both REPL modes.
- "Bad state impossible by construction" beats "bad state handled at
  runtime." When a finding offers both options, prefer the structural
  fix.

### Track CQ-A: Dead code removal

These modules ship with zero production callers, are wired through one
test, or cannot fire because an upstream cap blocks them. Removing each
also removes its tests and any docs claims that quietly assume the
module is live.

1. Remove `pipy_harness.native.dynamic_provider` (140 L wrapper around
   one `state.select_model` call, used only by its own test).
   Refs: `02:F18`, `07:F10`. **Done, and the capability (E5) is now
   ✅** — not by recreating the wrapper, but by verifying the live
   `/model` swap through the shared `NativeReplProviderState` boundary in
   both REPL product paths (`scripts/parity_checks/dynamic_provider_behavior.py`).
2. Remove `pipy_harness.native.approval_prompt` (410 L; ten reasons,
   six statuses, three value objects, zero non-test, non-re-export call
   sites). Refs: `07:F11`.
3. Remove `pipy_harness.native.session_branching` (157 L; recorder
   admits the wiring is deferred). Refs: `07:F13`.
4. Remove `pipy_harness.native.session_compaction` (210 L; cannot fire
   because `NativeConversationState.MAX_TURNS = 8` is below the
   compaction threshold). Refs: `07:F12`. If compaction is desired,
   first lift the conversation cap (Track CQ-D slice 5) and only then
   bring this back.
5. Remove `pipy_harness.native.image_attachment` (196 L; plumbed through
   `ProviderRequest.image_attachments` but consumed by no provider).
   Refs: `07:F14`. Re-introduce only when an actual provider parses it.
   **Reintroduced (D8 now ✅)**: a bounded, fail-closed `@image:` loader
   feeds `ProviderRequest.attachments`, which the Anthropic / OpenAI-Responses
   / Google adapters now render as native image blocks; both REPL paths
   wire it in and the archive keeps only safe metadata
   (`scripts/parity_checks/attachment_behavior.py`).
6. Remove `pipy_harness.native.themes` and remove the unused theme
   registry surfaces (test-only). Refs: `06:F22`. **Reintroduced (D7 now
   ✅)**: `themes.py` is the palette registry behind `chrome.ChromeStyle`,
   consumed by a real `/theme` command in both REPLs and resolved per
   render through `PIPY_THEME` (`scripts/parity_checks/theme_behavior.py`).
7. Remove `pipy_harness.native.skills`, `prompt_templates`,
   `custom_commands` and the chrome-side wiring that calls into them.
   Refs: `06:F1`. Reintroduce only when a runtime path consumes them.
   Until then, the banner stops advertising `[Skills]`, `[Prompts]`,
   and `[Extensions]` for inert paths.
8. ~~Remove the `BashTool` module until a real shell sandbox lands.~~
   **Done (shipped):** `pipy_harness/native/tools/bash.py` is registered as a
   real shell matching Pi — it spawns `bash -c <command>` in the workspace
   root with the inherited environment, an optional timeout (the process group
   is killed when it elapses), streams combined output live, and returns a
   bounded tail. Only metadata (counters, labels) is archived. B7 is a green
   behavior check. Refs: `03:F6`.
9. Remove the `TruncateTool` from the model-visible registry (pi treats
   it as an internal post-processing utility, not a model-facing
   tool). Refs: `03:F5`.
10. Remove the archive-side parallel tool family
    (`read_only_tool.NativeExplicitFileExcerptTool`,
    `patch_apply.NativePatchApplyTool`,
    `verification.NativeVerificationTool` — ~1,500 L) once the slash
    commands they back are migrated to call the model-driven
    `Read`/`Edit`/`Write` tools directly through the same
    archive-safe wrapper. Refs: `03:F1`, `03:F2`, `03:F8`.
11. Remove `pipy_session.catalog.verify_session_archive` and
    `reflect_on_finalized_sessions` (60+ L dead surface with an
    18-event registry, no production caller). Refs: `05:F5`, `05:F6`.
12. Remove the speculative `auto_capture.py` surfaces
    (`reference_pi_session`, `_public_model_from_argv`, most of
    `prune_auto_capture_state` — no production caller). Refs: `05:F9`.
13. Remove `pipy_harness.adapters.subprocess.SubprocessAdapter` if its
    only consumer is the test suite. If the "support path" claim is
    real, surface a runtime consumer. Refs: `04:F14`.
14. Remove `pipy_harness.sdk` if `__init__.py` already re-exports the
    same primitives. Or make `sdk` the documented surface and demote
    `__init__.py`. Choose one. Refs: `04:F20`.
15. Remove the `[Extensions]` banner section and the `ctrl+o` hint
    (advertised, unwired). Refs: `06:F3`, `06:F11`.

### Track CQ-B: Provider layer consolidation

Twelve adapters total ~7 KLoC for four wire shapes (OpenAI Responses,
OpenAI Chat Completions, Anthropic Messages, Gemini `generateContent`).
The bulk is mechanical duplication. pi-mono's
`openai-responses-shared.ts` is the model.

1. Extract a single `pipy_harness.native.http` module owning the
   `JsonHTTPClient` / `UrllibJsonHTTPClient` boundary and the four-class
   exception hierarchy. Delete the per-provider copies. Refs:
   `02:F1`, `02:F2`.
2. Extract `pipy_harness.native.providers._chat_completions_shared` for
   the Chat-Completions wire shape. Collapse OpenAI-Completions,
   OpenRouter, Mistral, and Cloudflare onto it (~760 L removed). Refs:
   `02:F3`.
3. Extract `pipy_harness.native.providers._responses_shared` for the
   OpenAI Responses wire shape and collapse the three current copies
   (`openai_provider`, `openai_codex_provider`, plus the Codex SSE
   path). Refs: `02:F16`.
4. Extract `pipy_harness.native.providers._anthropic_shared` and
   collapse Anthropic + Bedrock onto it. Refs: `02:F4`.
5. Move `_safe_response_label`, `_extract_usage`, and `_utc_now` into
   the shared http/parsing modules. Delete the per-provider copies.
   Refs: `02:F2`, `02:F13`, `02:F20`.
6. Wire `pipy_harness.native.retry.retry_with_policy` into every real
   provider HTTP entry point. Today it is well-tested but used by one
   of nine real providers. Refs: `02:F7`, `07:F15`.
7. Decide the streaming contract. Either (a) implement
   `StreamChunkSink` and `ReasoningSink` in every adapter that the
   protocol claims to support, or (b) remove the sink parameters from
   `ProviderPort.complete` and re-introduce them per-provider when a
   real streaming path exists. Today 10 of 11 real adapters accept
   them and immediately `del` them. Refs: `02:F6`.
8. Introduce a per-model registry module
   (`pipy_harness.native.model_registry`) that owns `max_tokens`,
   `supports_tool_calls`, `default_temperature`, default `max_tokens`,
   and reasoning-effort support per `(provider, model)`. Stop
   hardcoding `max_tokens=4096` across all Anthropic models, stop
   hard-coding `gpt-5.5` and `gpt-5.1-codex` as defaults, and let
   `Cloudflare` only advertise `supports_tool_calls=True` for models
   that actually support function calling. Refs: `02:F8`, `02:F9`,
   `02:F17`, `07:F9`.
9. Fix `GoogleProvider` / `GoogleVertexProvider` tool-call id
   fabrication: stop synthesizing ids from loop index and propagate the
   real id from the response. Refs: `02:F5`.
10. Fix the Codex OAuth refresh path so a refreshed token that omits
    `account_id` is rejected at refresh time, not the next request.
    Refs: `02:F10`.

### Track CQ-C: Bad-state-impossible refactors

Ronacher's rule applied directly: where pipy currently *handles* a bad
state, redesign the type so the bad state cannot exist.

1. Replace `ProviderResult` with a discriminated union (or
   factory-only constructors) so `SUCCEEDED` cannot carry an
   `error_message`, `PENDING` cannot appear from a completed call,
   `FAILED` cannot carry `tool_calls`. Delete the `__post_init__`
   guards. Refs: `07:F1`.
2. Replace `NativeToolSandboxPolicy` / `NativeToolApprovalPolicy`
   with mode-tagged value objects whose fields cannot be set
   incoherently (e.g. `NO_WORKSPACE_ACCESS` cannot have
   `workspace_read_allowed=True`). Refs: `07:F2`.
3. Close the metadata-key universe: convert the 27 frozensets of
   string keys in `models.py` into `Literal[...]` types or enums.
   Make the archive-safe allowlist a type, not a runtime check. Refs:
   `07:F6`.
4. Make `NativeRunInput.system_prompt_id` / `system_prompt_version`
   `Literal[...]` once they have one production value each. Refs:
   `07:F3`.
5. Validate `NativeToolRequest.tool_kind` against a closed enum at
   construction; do not accept `str`. Refs: `07:F4`.
6. Remove the nine "always False" storage booleans from
   `NativeTurnMetadata.archive_payload()` and reflect the actual policy
   in the type instead. Refs: `07:F5`.
7. Replace `recorder.finalize_session` recovery branch with a
   constructor-time check that finalize cannot be called twice or on
   an already-renamed directory. Today the recovery branch *handles*
   the case. Refs: `05:F1`.
8. Make `recorder.append_event` refuse appends to a finalized record
   structurally (no `.in-progress/pipy` path → no append), instead of
   relying on the caller not to call. Refs: `05:F2`.
9. Fix `_unique_path` so the canonical filename round-trips through
   `FILENAME_RE` instead of being mangled. The "uniqueness" suffix
   should not break the format. Refs: `05:F3`.
10. Stop swallowing every `(OSError, UnicodeError,
    json.JSONDecodeError)` in catalog readers. A finalized record that
    will not parse is a recorder bug, not a catalog edge case. Refs:
    `05:F7`. The downstream `verify_session_archive` surface that
    exists to compensate for this can then be deleted (already in
    Track CQ-A).
11. Bind `NativeVerificationRequest.command_label` to a closed enum;
    do not accept arbitrary 80-char strings into a "safe label"
    position. Refs: `07:F16`.
12. Replace the free-form `request_source == "pipy-owned-human-reviewed"`
    check in `NativePatchApplyRequest` with a discriminator enum that
    only the pipy-owned construction site can produce. Refs: `07:F22`.

### Track CQ-D: Structural simplification

Collapse the parallel families.

1. Collapse `NativeAgentSession`, `NativeNoToolReplSession`, and
   `NativeToolReplSession` into one session driven by an explicit
   state machine. The no-tool REPL's shadow slash-command
   implementations of `/read`/`/ask-file`/`/propose-file`/
   `/apply-proposal` re-routes to the same tools the
   tool-loop session uses. Refs: `01:F3`, `01:F2`.
2. Replace the 350-line `if/elif` REPL command-dispatch chain in
   `session.py` with a command table (name → handler + descriptor).
   The chrome menu, the help printer, and the dispatcher all read the
   same table. Refs: `01:F4`.
3. Centralize the slash-menu / readline / prompt-toolkit / plain
   adapters behind one input port and remove the hand-rolled ANSI
   cursor logic from the slash-menu adapter (~280 L). Refs:
   `01:F18`, `01:F19`.
4. Collapse `PipyNativeAdapter`, `PipyNativeReplAdapter`, and
   `PipyNativeToolReplAdapter` into one adapter parameterized by
   `RunMode` (one-shot / no-tool-repl / tool-loop-repl). They already
   share the name `pipy-native`. Refs: `04:F1`.
5. Decide whether `AgentPort` is real polymorphism (subprocess +
   native + future) or a phantom protocol with one consumer. If
   phantom: inline. If real: write the missing consumer (e.g. a
   real RPC adapter) before the protocol is allowed to stay. Refs:
   `04:F2`.
6. Split `pipy_session.catalog` (1,179 L) into focused modules:
   `catalog/list.py`, `catalog/search.py`, `catalog/inspect.py`. Drop
   `verify` and `reflect` per Track CQ-A. Refs: `05:F4`.
7. Split `pipy_harness.native.read_only_tool` (715 L) into the
   archive-safe one-call boundary it claims to be plus a separate
   path-validation helpers module shared with the model-driven tools.
   Refs: `03:F2`.
8. Lift `NativeConversationState.MAX_TURNS = 8` for the interactive
   REPL. The cap is currently shipping a UX bug (the REPL refuses
   turns at 8) and silently disables `session_compaction` (Track
   CQ-A slice 4). Refs: `07:F7`.
9. Consolidate the 13-way provider switch in `repl_state.py` (four
   copies) into one provider-descriptor table that owns every
   per-provider fact. Refs: `02:F19`, `07:F8`.
10. Resolve the dual `ToolPort` Protocol name clash by giving the
    archive-safe variant a distinct name (e.g. `ArchiveToolPort`) or
    by deleting the archive-side family per Track CQ-A slice 10.
    Refs: `03:F1`.

### Track CQ-E: Plausible-but-wrong correctness fixes

Concrete bugs surfaced by the audits. Each warrants a focused test.

1. Re-order `session.finalized` emission to fire *after*
   `recorder.finalize()` completes, not before. Wrap the failure path
   in `finally` so finalization is guaranteed. Refs: `04:F7`, `04:F8`.
2. Resolve the chrome banner / loader path disagreement. Pick one
   canonical layout for global resources (currently chrome says
   `~/.pipy/...`, loader says `~/.config/pipy/...`) and one for
   workspace resources (chrome says `.pipy/commands` for prompts,
   loader says `.pipy/templates`). Refs: `06:F4`, `06:F5`, `06:F6`,
   `06:F7`.
3. Stop printing `final_text` to `sys.stdout` from inside
   `PipyNativeAdapter.run`. The adapter does not own stdout. Refs:
   `04:F21`.
4. Stop instantiating a real provider in `_resolve_repl_mode` just to
   read `supports_tool_calls`. Derive the capability from the
   model-registry (Track CQ-B slice 8) without an HTTP-capable
   instance. Refs: `04:F9`.
5. Disambiguate the `harness.run.failed` event. It is emitted from
   two code paths with different semantics (adapter returned bad
   status vs exception escaped adapter); split into
   `harness.run.adapter_failed` and `harness.run.exception`. Refs:
   `04:F16`.
6. Fix `_resource_files.discover_resource_files`: enforce the workspace
   byte cap *before* `_read_capped_bytes` streams the whole file, not
   after. Today an over-cap file is read fully and then discarded.
   Refs: `06:F14`.
7. Fix `_resource_files._path_label_for` so symlink resolution does
   not lose the workspace prefix. Refs: `06:F15`.
8. Fix `_load_first_candidate` so a seen candidate falls through to
   the next candidate in the same directory, instead of returning
   `None` for the whole directory. Refs: `06:F16`.
9. Tighten the two competing secret detectors (`looks_sensitive`
   substring vs `has_secret_shaped_content` regex) into one helper
   with one definition. Apply at one layer. Refs: `03:F9`.

### Track CQ-F: Deduplication

Remove copy-paste that the type system could have caught.

1. Deduplicate `_safe_component`, `_filename_stamp`,
   `_looks_sensitive`, and `_redacted_argv` between
   `pipy_harness.capture` and `pipy_session.auto_capture` (~80 L).
   Refs: `05:F16`.
2. Deduplicate the three near-identical `_validate_safe_label` /
   `_validate_scope_label` helpers. Refs: `07:F24`.
3. Replace the six identical footer-repaint call sites with one
   `_redraw_footer()` helper that reads from a single source of
   truth. Refs: `01:F5`.
4. Replace `_final_status` / `_native_error_type` /
   `_native_error_message` with one dispatcher that returns the
   tuple. Refs: `01:F17`.
5. Stop swallowing `ValueError` on `ProviderToolCall` construction
   in 11 places. Move the construction into a single helper. Refs:
   `02:F12`.
6. Consolidate the chrome status block's overloaded
   `context_budget_suffix` (currently encoding two distinct facts in
   one field). Refs: `06:F20`.
7. Delete duplicate byte-cap checks in `discover_resource_files`
   (three checks for the same value, one structurally unreachable).
   Refs: `06:F12`.
8. Consolidate the eleven copies of `_extract_usage` into the shared
   provider module (Track CQ-B). Refs: `02:F13`.

### Out Of Scope For This Track

These remain explicitly deferred and are not slices of the audit
track:

- Rewriting pipy in TypeScript or porting pi-mono's TUI library.
- Adding pydantic, typebox, jsonschema, attrs, or any other
  validation/typing runtime dependency.
- Adding multi-agent orchestration, RPC mode, full TUI, persistent
  history, or extension/plugin loaders.
- Re-introducing dead modules removed in Track CQ-A as a "future"
  hedge. They come back only when a runtime path consumes them.
- Touching the public archive privacy invariants. The audit's bad-
  state fixes (Track CQ-C) tighten them; they never relax them.

### Cross-cutting reminders

- Every slice in this track ships with a focused test, a green
  `just check`, an updated `docs/architecture.md` codebase map row if
  a file moves or disappears, and a conventional commit.
- Slice ordering is a recommendation, not a constraint. Pick the
  highest-leverage slice from any track that fits the next review
  cycle.
- Each audit finding cites file:line in the audit file. The audit
  files are the authoritative detail; this section is the planning
  index.

## Done

Historical done ledger preserved for documentation-contract tests:
Native inert read-only tool request value objects.
Native explicit file excerpt read-only tool implementation.
Native provider-visible repo context policy.
Native bounded read-only tool observation into follow-up provider turn.
file excerpts, proposal drafts, patch text, verification output.
Native approval and sandbox enforcement baseline; Native inert read-only tool
request value objects; Native explicit file excerpt read-only tool
implementation; OpenAI subscription-backed native auth decision
`blocked-for-now` on 2026-05-07 because unsupported credential scraping and
CLI/product wrapping are rejected; Native OpenRouter Chat Completions provider
with `--native-provider openrouter --native-model <provider/model>` and
`OPENROUTER_API_KEY`; Native bounded post-tool provider turn against synthetic
sanitized observations; Native bounded read-only tool observation into
follow-up provider turn; Native patch proposal boundary before writes; Native
provider-visible repo context policy; Native supervised patch apply boundary
using NativePatchApplyRequest and native.patch.apply.recorded; Native
allowlisted verification-command boundary using NativeVerificationRequest and
native.verification.recorded; First supervised self-bootstrap trial
implementation as a test-only trial; First supervised self-bootstrap review;
Product-direction checkpoint after first native smoke test toward a Pi-like
native shell.

Native conversation state and bounded provider-turn loop foundation:
pipy_harness.native.conversation, metadata-only per-turn payloads, Native
one-shot run rebased on conversation state, provider turn indexes and labels,
per-run in-memory native conversation identity/state. Native minimal no-tool
REPL: `pipy repl --agent pipy-native`, `no_tool_repl`. Native visible approval
and sandbox prompt foundation: stream-based approval resolver and attempted
capability escalation. Native interactive read-only REPL command behind the
prompt gate: `/read <workspace-relative-path>` records only metadata-only tool
lifecycle events. Native explicit provider-visible `/ask-file` REPL boundary:
`/ask-file <workspace-relative-path> -- <question>` labeled `ask_file_repl`.
Native `/ask-file` smoke and separator hardening used a whitespace-delimited
`--` separator; OpenRouter smoke was skipped. Native REPL command help and
usage diagnostics added local `/help` command and unsupported slash commands;
Native REPL command help and usage diagnostics review second review reported
no findings and All four were accepted and fixed.

Native REPL next-boundary decision selected a proposal-only
`/propose-file <workspace-relative-path> -- <change-request>` path. No runtime
behavior changed. Native proposal-only `/propose-file` REPL boundary now
accepts `/propose-file <workspace-relative-path> -- <change-request>` labeled
`propose_file_repl`. Native proposal-only `/propose-file` review and smoke:
fake-provider terminal smoke; No implementation hardening was required. Native
REPL next-boundary decision after proposal-only review selected a human-applied
proposal trial and public REPL stays proposal-only. OpenAI Codex OAuth provider
correction from Pi reference selected a distinct `openai-codex` provider path
using packages/ai/src/utils/oauth/openai-codex.ts and
packages/ai/src/providers/openai-codex-responses.ts at
https://chatgpt.com/backend-api/codex/responses. Pi-like no-approval shell
direction correction: No permission popups, packages/coding-agent/src/core/tools/read.ts.
Native REPL approval prompt removal uses `not-required` approval policy data
and is no longer wired into the normal product REPL path.

Native `openai-codex` OAuth provider from Pi reference:
`--native-provider openai-codex --native-model <model>`,
`pipy auth openai-codex login`,
`${PIPY_AUTH_DIR:-~/.local/state/pipy/auth}/openai-codex.json`. Native OpenAI
Codex provider SSE transport correction: SSE Responses request with
`stream: true` to `https://chatgpt.com/backend-api/codex/responses`. Native
REPL auth/model commands and late-bound provider selection: `pipy` now starts
the native REPL; `/login [openai-codex]`, `/logout [openai-codex]`; model
selection is resolved before each provider-visible turn. Native human-applied
`/propose-file` trial through shell auth/model commands used
`/model openai-codex/gpt-5.2`, secret_looking_content, and was useful enough
to justify a narrow write-capable boundary design slice.

Native one-file `/apply-proposal` REPL command:
/apply-proposal <workspace-relative-path>, same-session `/propose-file`,
NativePatchApplyRequest, native.patch.apply.recorded. Native REPL `/verify
just-check` command: NativeVerificationRequest, native.verification.recorded.
Native REPL `/verify just-check` review and smoke: Fake-provider terminal smoke
runs exercised propose/apply/verify success; `pipy-session verify`, `list`,
`search`, and `inspect` remained compatible. Native first pipy-applied,
pipy-verified tiny change: 2026-05-11, `openai-codex/gpt-5.2`,
`/propose-file pyproject.toml -- <change-request>`,
`/apply-proposal pyproject.toml`, `/verify just-check`,
`native-self-bootstrap-trial`, no runtime dependencies are declared.

Native next-boundary decision after the first self-bootstrap trial:
summary-safe inspection of the finalized `native-self-bootstrap-trial`; The
selected next boundary is therefore a failed-read recovery slice. Native
bounded read-failure recovery for explicit REPL file commands: one failed or
skipped read attempt can happen before that successful excerpt; Archive
payloads remain metadata-only and add only safe budget booleans. Native
bounded read-failure recovery review and smoke: split-budget implementation
aligned with the selected contract; local `/help`, `/model`, `/apply-proposal`,
and `/verify just-check`; fake-provider REPL smoke exercised failed-read
recovery.

Native no-tool REPL conversation-context decision after read-failure recovery
review selected bounded in-memory context for ordinary no-tool REPL turns under
explicit turn and byte limits. File excerpts, proposal drafts, patch text,
verification output are excluded. The decision slice changed no runtime
behavior. Native bounded no-tool REPL conversation context:
`NativeNoToolReplConversationContext`, 4 KiB provider-visible byte budget,
clears on login, logout, provider/model changes; raw prompts, provider final
text, excerpts are not archived. Native bounded no-tool REPL conversation
context review and smoke: two-round independent review cycle, second round
reported zero findings, implementer-side closeout audit, fake-provider REPL
smoke with two ordinary turns. The next selected native-shell boundary is a
local `/clear` command.

Native local `/clear` REPL command now accepts `/clear` as a local command;
malformed `/clear <text>` stays local and does not clear history; does not
reset provider/model selection, auth state, read budgets. Native local
`/clear` review and smoke: two-round independent review cycle, two
suggestion-level test coverage items, both were accepted and fixed,
post-clear verification availability coverage, second review found no
findings, fake-provider `/clear` REPL smoke. Native next-boundary decision
after `/clear` review and smoke. Native next-boundary decision after
`/clear`: summary-safe archive reflection found the `/clear`
implementation review cycle clean; The selected next boundary is a local
`/status` REPL command. This decision slice changed no runtime behavior.
Native local `/status` REPL command now accepts `/status` as a local command;
pending proposal availability, and verification availability; archive raw
command text remains forbidden.

Native next-boundary decision after `/status` selected next boundary is
Pi-like REPL startup chrome. This is a user-facing shell ergonomics slice.
Native Pi-like REPL startup chrome: bare `pipy` and `pipy repl --agent
pipy-native` now print compact chrome derived from the same safe display state
used by `/status`. Native next-boundary decision after startup chrome selected
next boundary is a Pi-like visual/resource-label pass. Native Pi-like startup
visual/resource-label pass: ANSI title/section/dim styling only for suitable
TTY streams and existence-level workspace-relative resource source labels.
Local Zensical documentation preview/build: `just docs-serve` starts the local
preview server; `just docs-build` builds the static site; Zensical is a
dev/tooling dependency only.

Native grouped slash-command discovery: one stable grouped command reference
on stderr for controls, local state, provider/model, file context, proposal.
Native post-help input ergonomics decision selected one more line-oriented
implementation boundary. state-aware prompt label before each input. Native
line-oriented state-aware prompt label replace the fixed prompt with a compact
stderr prompt label. Native terminal-layer direction checkpoint selected a
narrow `prompt-toolkit` line-editor adapter investigation; Textual was judged
too application-like; current plain line-oriented runtime as the required
fallback. Native prompt-toolkit line-editor feasibility boundary:
`NativeNoToolReplSession` now reads input through a small internal adapter;
`--input-runtime plain|prompt-toolkit|auto`; safe `input_runtime` label.

Native prompt-toolkit slash-command completion boundary: leading slash-command
completer; Prompt-toolkit remains an optional opportunistic line-editor path;
Focused tests cover the attached completer. Native prompt-toolkit file/path
completion boundary suggests existing workspace-relative path labels and
command handlers remain the source of truth. Native prompt-toolkit multiline
input boundary: Enter submits the current buffer; Esc+Enter inserts a newline.
Native prompt-toolkit bottom-toolbar status decision: defer bottom-toolbar
behavior. real-TTY prompt-toolkit hardening pass found an async completion
protocol compatibility gap. Native prompt-toolkit real-TTY input hardening
disables prompt-toolkit cursor-position requests and handles CR and LF terminal
encodings.

Native prompt-toolkit next-boundary decision after real-TTY hardening selected
prompt-toolkit-only `@file` reference completion. Completion-only. Resilient
resize behavior was rejected. Persistent history was rejected. Bottom-toolbar
behavior remains deferred. Native prompt-toolkit `@file` reference completion
boundary suggests safe workspace-relative `@file` labels. Accepting a
completion inserts only text and does not read files, attach context, invoke
providers or tools. Native next-boundary decision after `@file` completion
selected a narrow explicit multi-file context budget: two successful
workspace-relative excerpts per REPL session. Automatic `@file` reads,
model-selected paths remained deferred at that point. Native provider-visible
repo context policy is complete. (Update: user-directed `@file` context has
since shipped — a submitted prompt's `@path` references load bounded excerpts
through the shared bounded reader in both REPL modes and the product TUI.)

Native tool-loop TUI shell: real-TTY tool-loop sessions now use a pipy-owned
alternate-screen terminal UI with retained startup/context rows, submitted
prompt bands, active assistant output, transient working state, compact shaded
model-selected tool rows, footer/status pinning, slash-menu input behavior, and
active provider-turn Escape that renders red `Operation aborted` while
suppressing late chunks. The product TUI slash menu now lists only executable
local tool-loop commands (`help`, `exit`, `quit`). The slice shipped with
stdlib ANSI screen-cell verification, tmux product-path artifacts, Pi comparison
artifacts, focused TUI/renderer tests, docs updates, `just check`, and a clean
second review after the inert-command menu finding was fixed. (This shell was
later reworked into the inline-scrollback model with full-height use, native
scrolling, the `/copy` command, and the interactive `/model` selector — see Pi
Gap Queue items 2 and 3 above for the current behavior; the menu now lists
`help`, `model`, `settings`, `copy`, `exit`, `quit`.)

## Next Slice

### Extension API slice 6: input + before_agent_start hooks and send_user_message

Slices 1–5 have **landed**:

- Slice 1 (discovery + manifest inventory, no execution):
  `pipy_harness.native.extensions.discover_extensions` returns deterministic
  loadable/disabled `ExtensionDescriptor` records, parses optional
  `pipy-extension.toml` with stdlib `tomllib`, fails closed on unsafe
  names/paths/manifests/api_versions/duplicates/binary entries, and never
  imports extension code. Gate:
  `scripts/parity_checks/extension_discovery_conformance.py --json`.
- Slice 2 (activation sandbox boundary):
  `pipy_harness.native.extension_runtime.activate_extensions` imports only
  `loadable` descriptors, calls `activate(api)` (sync or async), supports
  `register_command` only via the public `pipy_harness.extensions.PipyExtensionAPI`,
  and fails closed per extension on import / no-activate / activation-exception /
  invalid / duplicate / reserved command name. Disabled descriptors are never
  imported. Gate:
  `scripts/parity_checks/extension_activation_conformance.py --json`.
- Slice 3 (command dispatch): activated extension `/<command>`s dispatch through
  the live tool-loop REPL (`dispatch_extension_command`), after built-ins and
  custom commands (no shadowing) and before the not-handled fallback, running
  the handler with a mode-aware context and the raw args, emitting `ctx.ui.notify`
  output as live UI, with **no provider turn**. Names/descriptions appear in the
  slash menu; `/reload` re-activates. Gate:
  `scripts/parity_checks/extension_dispatch_conformance.py --json`.
- Slice 4 (`tool_call` policy hook): an extension registers
  `@api.on("tool_call")` (or `api.on("tool_call", handler)`) to inspect a
  model-selected tool call's live name + parsed input before execution and
  return `ToolBlock(reason=...)` to block it. Wired into the tool loop
  (`dispatch_tool_call_hooks` before `_invoke`); first block wins; a crashing
  hook fails closed; raw inputs are inspected live but not archived. Gate:
  `scripts/parity_checks/extension_tool_call_conformance.py --json`.
- Slice 5 (lifecycle events): `session_start`, `session_shutdown`,
  `agent_start`, `agent_end`, `turn_start`, and `turn_end` fire to `@api.on(...)`
  observers via an `_ExtensionAwareEmitter` wrapping the automation emitter
  (`dispatch_lifecycle_hooks`); observe-only, fail-soft (a crashing observer
  never breaks the session), `LifecycleEvent` carries only the event name +
  session-start reason. `/reload` refreshes the observers. Gate:
  `scripts/parity_checks/extension_lifecycle_conformance.py --json`.

The selected next implementation slice adds the `input` and
`before_agent_start` hooks plus `send_user_message`: `input` may observe or
transform a submitted prompt before a turn; `before_agent_start` may inject
bounded safe context or alter system-prompt options; and `api.send_user_message`
lets an extension enqueue a deterministic provider turn (enough for a command to
trigger one). Prompts/injected context are inspected/added live but not archived
beyond existing safe metadata.

Acceptance criteria:

```sh
uv run pytest tests/test_native_extension_input_hooks.py
just check
```

The expected follow-up slice is pure/read-only extension tool registration.

## Near Term

The near-term product direction is a real `pipy-native` runtime with a Pi-like
interactive shell. The shell should be a thin user interface over pipy-owned
provider, session, turn, tool, sandbox, and archive boundaries, not a separate
runtime and not a wrapper around Codex, Claude, Pi, or another agent CLI. The
product posture is explicitly Pi-like: no permission popups for normal
interactive use.

Provider access direction: OpenAI Codex subscription auth remains the preferred
near-term hosted real-provider path. The existing `openai` provider remains the
pay-by-token OpenAI Platform API-key baseline; the subscription path is the
separate `openai-codex` provider modeled on Pi's PKCE OAuth and
`chatgpt.com/backend-api/codex/responses` implementation. OpenRouter is useful
for ad-hoc smoke testing with `OPENROUTER_API_KEY` but is not the preferred
default. Anthropic subscription access is not a near-term native provider
target because subscription-backed coding-agent usage is expected to stay
within Claude Code. The first selected local integration is `ds4`, using
`deepseek-v4-flash` through a local OpenAI-compatible Chat Completions server;
it is registered as tool-loop capable after live ds4 smoke proved OpenAI-style
tool-call round trips with pipy's loop.

The current implementation target is in `Next Slice` above.

Historical near-term gates that remain implemented or intentionally preserved
as context: The Tool-Loop Parity Track and the follow-up OpenAI Responses +
OpenAI Codex Tool-Call Parity Track have both landed end-to-end. The broader
slopfork direction is Pi parity, and the input-adapter boundary are the first
visible parity steps. OpenAI Codex subscription auth as the preferred
near-term real-provider path; OpenRouter remains implemented and useful for
immediate manual smoke testing. No-tool provider-turn REPL gate: available now
through `pipy repl --agent pipy-native`; Later ordinary no-tool turns now
receive bounded in-memory history. Historical visible approval prompt gate.
Narrow read-only shell command gate: available now. Provider-visible
interactive context gate: available now through
`/ask-file <workspace-relative-path> -- <question>` with whitespace-delimited
`--` separator. Command help and usage-diagnostic gate: available now.
Proposal-only interactive file gate: available now through
`/propose-file <workspace-relative-path> -- <change-request>` labeled
`propose_file_repl`; Proposal-only review gate: available now, implemented,
reviewed, and trialed with a real `openai-codex` provider turn. One-file
write-boundary decision gate: available now; the public mutation command is
`/apply-proposal <workspace-relative-path>`. Allowlisted verification gate:
available now. Local conversation clear gate: available now through `/clear`,
reviewed and smoked. Next-boundary decision gate after local clear: available
now selected a local `/status` command as the next native-shell boundary.
Local status command gate: available now through `/status`, showing retained
no-tool history counts and byte counts, explicit-read budget booleans, pending
proposal availability, and verification availability. Pi-like startup chrome
gate: available now. Pi-like visual/resource-label decision gate: available
now. Pi-like startup visual/resource-label gate: available now.
Input-ergonomics decision gate: available now. Grouped slash-command discovery
gate: available now. Post-help input ergonomics decision gate: available now.
Line-oriented state-aware prompt label gate: available now.
Terminal-layer direction checkpoint gate: available now. Prompt-toolkit
line-editor feasibility gate: available now. Prompt-toolkit slash-command
completion gate: available now. Prompt-toolkit file/path completion gate:
available now. Prompt-toolkit multiline input gate: available now.
Prompt-toolkit bottom-toolbar status decision gate: available now with
bottom-toolbar status decision and deferred footer behavior. Prompt-toolkit
real-TTY input hardening gate: available now and handles both CR and LF
encodings. Prompt-toolkit next-boundary decision gate: available now selected
prompt-toolkit-only `@file` reference completion. Prompt-toolkit `@file`
reference completion gate: available now with safe workspace-relative `@file`
labels. Next-boundary decision gate after `@file` completion: available now,
two successful user-named file excerpts per REPL session, line-oriented and
privacy-safe. Read-failure recovery review gate: available now. Historical
visible approval prompts were removed from the normal product REPL path.
Self-bootstrap readiness gates remain historical context.

Invariants that must hold for any near-term slice:

- default native stdout remains successful final text only on success, with
  diagnostics, finalization, progress, and errors on stderr
- the existing `pipy-session` metadata archive and `--native-output json`
  remain metadata-only and never include raw prompts, model output, provider
  responses, request bodies, raw patch text, raw diffs, file contents, raw tool
  observations, command stdout, command stderr, auth tokens, cookies,
  credentials, secrets, private keys, or sensitive personal data; this does not
  prohibit the separate private native product session tree from storing the raw
  conversation needed for Pi-style resume and `/tree`
- metadata records still pass `pipy-session verify`, and `pipy-session list`,
  `search`, and `inspect` stay compatible

## Deferred

### Deferred For Self-Bootstrap

- Full tool-capable native pipy agent runtime beyond the provider,
  conversation, approval, sandbox, and tool-boundary slices.
- General native model/tool loop beyond bounded provider turns and explicitly
  approved tool boundaries. The bounded Pi-shaped slice of this work is now
  planned as the `Tool-Loop Parity Track` above; broader model/tool-loop
  capabilities outside that track stay deferred.
- Arbitrary shell execution. **Update (shipped):** the model-driven `bash` tool
  is now a real shell matching Pi — arbitrary commands run in the workspace
  (`bash -c <command>`), with combined bounded output and an optional timeout.
  Only metadata is archived.
- Project-defined verification policy beyond the Pi-style model-visible `bash` workflow. The
  former `/verify just-check` command has been removed from the user-facing REPL.
- Broad repo maps or persistent workspace summaries beyond the first bounded
  provider-visible context policy.
- Local model provider integrations for Ollama, llama.cpp, MLX, LM Studio, or
  similar runtimes until separate benchmark work identifies the best first
  local runtime and connection shape.
- Generic OpenAI subscription-backed native provider auth beyond the distinct
  `openai-codex` provider path until official OpenAI docs expose a stable
  third-party/native provider auth flow that is not specific to Codex,
  ChatGPT, or another OpenAI product client.

### Deferred For Product Maturity

- Codex JSONL event adapter.
- Claude integration beyond the existing conservative `pipy-session auto`
  metadata capture.
- Session export/share polish after the native product session tree lands:
  HTML export, private share/upload flow, and any broader cross-project native
  session management not covered by the conformance gate.
- Raw transcript import with explicit opt-in and redaction policy.
- Indexed archive search or SQLite-backed query layer.
- Review-cycle metadata shape for summary-safe appended events, including
  explicit per-round versus cumulative scope, review round number, and optional
  cycle identity so future archive analysis does not double-count iterative
  reviews. The former `pipy-session workflow` and `reflect` commands have been
  removed.
- Full interactive TUI behavior beyond the shipped product TUI shell. Prompt
  history, bracketed paste, undo/redo, resize/SIGWINCH handling, an interactive
  `/settings` control dialog, and optional persistent cross-session prompt
  history (off by default, local-only state file) now ship in the product TUI;
  still deferred are `@` file picker behavior (scored, not fuzzy), broader path
  completion in the product editor, clipboard/drag image paste, `!`/`!!` shell
  shortcuts, thinking-level hotkeys, output/thinking folding, queued
  steering/follow-up messages during active turns, richer overlays and
  selectors, mouse selection, and theme/extension UI hooks. True
  provider-request cancellation (Escape/Ctrl-C abort the in-flight HTTP request)
  now ships. Scoped model cycling via `/scoped-models` + Ctrl+P now ships through
  the settings track.
- General extension/package platform: Python-only Pi-shaped extension API,
  extension-registered tools/commands/providers/keybindings/UI, third-party
  package install/update/list/config flows, package manifests, and the
  corresponding security/update model. The draft target specification is
  [extension-api.md](extension-api.md).
- Provider/model catalog follow-ons after the selected closeout slices: live
  Anthropic/Copilot login UX and extension-registered providers once the
  extension platform exists.
- RPC and automation modes beyond `pipy run`, `--native-output json`, and the
  in-process Python SDK: JSON event-stream mode, stdin/stdout RPC, and a
  long-running process-integration protocol.
- Product distribution and sharing polish: documented package install/update
  path, self-update flow, changelog surface, HTML export, and private
  share/upload workflow.
- Project-defined verification policy beyond the Pi-style model-visible `bash`
  and future extension-gate workflow.
- Multi-agent task delegation.
- Long-running dev server.

Historical deferral wording retained for tests: additional OAuth providers;
Full interactive TUI beyond the selected narrow `prompt-toolkit`; Textual or
another full-screen TUI framework; RPC mode.

## Explicitly Not Now

- Making Codex, Claude, or another coding-agent CLI wrapper the main product
  path.
- Storing full system prompts, user prompts, model outputs, stdout, stderr,
  tool payloads, secrets, tokens, credentials, private keys, or sensitive
  personal data in the `pipy-session` metadata archive, `--native-output json`,
  docs, or synced artifacts by default. The private native product session tree
  is the explicit Pi-like exception for raw conversation history.
- Building broad approvals, sandboxing, retries, streaming, raw transcript
  import, multiple native tool requests, post-tool provider turns, general write
  tools beyond supervised patch apply, non-allowlisted verification commands,
  Textual or another full-screen TUI framework, RPC, or orchestration
  opportunistically. Provider registry/catalog work is allowed only inside the
  selected provider-catalog track and its conformance gate; additional OAuth
  providers remain part of that track's reviewed milestones, not opportunistic
  side work.
- Using unsupported subscription auth, scraping browser or CLI session stores,
  or treating another product's login/session as pipy-native provider
  credentials.

## Runtime Resource Loading Track (landed 2026-05-30)

Closes parity rows D4 (skills), D5 (prompt templates), and D6 (custom slash
commands) with real runtime behavior, not file-existence rubber-stamps. This
is deliberately **not** a general extension API: only three bounded resource
kinds load, through the existing provider/session/tool/archive boundaries.

What shipped:

- `pipy_harness.native._resource_files` (shared discovery), `skills`,
  `prompt_templates` (with `$ARGUMENTS`/`$1..$9` expansion), and
  `custom_commands` loaders were reintroduced **with** a runtime consumer.
  Discovery is workspace-first then global (`PIPY_CONFIG_HOME` →
  `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy`), `*.md` one level deep,
  deduped by canonical path. Safety policy rejects secret-shaped filenames,
  binary content (NUL byte), generated/`.gitignore`-matched filenames,
  oversized bodies (per-file + total byte caps with truncation marker), and
  symlink-escapes.
- `pipy_harness.native.resources` is the registry + pure
  `dispatch_resource_command` consumed by both REPL product paths. `/skill`
  and `/template` list (bare) or run (named); custom `/<name>` commands run
  through the same local-command boundary as built-ins and cannot shadow a
  reserved built-in name. Unknown/unsafe/empty resources fail closed with no
  provider turn.
- Wiring: `session.py` (no-tool) and `tool_loop_session.py` (tool loop +
  product TUI). The TUI slash menu and the no-tool completion set advertise
  `/skill`, `/template`, and discovered custom commands; no-tool-only
  commands stay out of the tool-loop menu. The `[Skills]` chrome section now
  lists loadable skill names from the loader.

Privacy: only safe counters/labels are recorded. The no-tool path emits
`native.resource.invoked` / `native.resource.rejected` events carrying
`{resource_kind, name, path_label, sha256, byte_length, truncated}` and a
`resource_invocation_count` in the completion event; the tool-loop path
returns `resource_invocation_count` in `NativeToolReplResult`. Resource
bodies, expanded prompts, and command text never reach JSONL, Markdown
summaries, `--native-output json`, prompt history, or the transcript sidecar.

Verification: unit tests for parser/discovery/precedence/safety and the
dispatcher; no-tool and tool-loop product-path tests (incl. archive non-leak);
real-PTY product-TUI tests at 80x24 and 100x40 for custom-command
discovery/execution and unsafe-resource rejection. The D4/D5/D6 parity-score
checks are behavior checks (`scripts/parity_score.sh`).

Out of scope for *this* track (skills/templates/commands): a general
extension/package loader and runtime UI hooks. Themes (D7) and image
attachments (D8) have since landed on their own — see the Native Session
Workflow / parity rows and `docs/pi-parity.md`.

## Native Session Workflow Track (landed 2026-05-30)

Closes parity rows E2 (session compaction) and E3 (session branching) with
real product behavior, and upgrades E1 (session resume) from a metadata-only
reader to a live runtime resume. Metadata-first archive defaults stay
mandatory throughout.

> **Superseded (2026-06-09):** the metadata-only `--resume RECORD` /
> `--branch LABEL` repl flags described below were **retired** in favor of the
> native product session tree ([session-tree.md](session-tree.md)), which is now
> the product session source for resume/branch/fork. This section is retained as
> historical record; `pipy-session resume-info` remains the separate archive
> utility.

What shipped (historical; `--resume`/`--branch` repl flags now retired):

- **Live resume.** `pipy repl --agent pipy-native --resume <stem>` seeds a
  fresh no-tool or tool-loop session from the existing metadata-only
  `ResumeContext`/`compose_resume_system_block` (prior provider/model/turn
  labels only). The prior finalized record is never mutated and no raw
  transcript sidecar is copied. Both REPL surfaces show a safe resumed-state
  banner; the tool-loop product TUI commits it to scrollback at startup.
- **Branch/fork.** `pipy repl --resume <stem> --branch <label>` forks a child
  with a validated safe label (`--branch` requires `--resume`; unsafe labels
  fail closed via `validate_branch_label`). `pipy_harness.models.SessionLineage`
  carries the safe parent id, relationship, branch label, fork timestamp, and
  prior provider/model/turn counters.
- **Compaction.** `pipy_harness.native.session_compaction` is a pure
  transformation. `/compact` (and an automatic threshold) reduce the
  provider-visible context while keeping recent turns plus a safe metadata-only
  summary appended to the system prompt. The no-tool path compacts its bounded
  exchange context; the tool-loop cuts the `LoopMessage` history only at
  `UserMessage` group boundaries, so compaction never orphans a tool result,
  reorders a tool-call/observation pair, or exposes a raw tool payload.
- **Archive + catalog.** The runner writes a safe `resume` object onto
  `session.started` and emits `native.session.resumed`; compaction emits
  `native.session.compacted` counters. `pipy-session list/inspect/export/
  resume-info` surface the lineage and compaction metadata read-only and reject
  malformed/ambiguous/symlinked/active/out-of-archive records without leaking
  bodies.

Verification: unit tests (compaction trigger/state reduction + protocol
validity, lineage/branch-label validation, reader safety, runner archive
wiring); no-tool product tests (`--resume`, `--branch`, `/compact`, automatic
threshold, rejections, parent immutability); tool-loop product tests
(resumed/compacted provider requests, valid tool-message history, archive
non-leak); real-PTY product-TUI tests at 80x24 and 100x40 for resumed-state
visibility and `/compact`. The E2/E3 parity-score rows are behavior checks
(`scripts/parity_checks/compaction_behavior.py`,
`scripts/parity_checks/branching_behavior.py`).

Superseding direction: this landed track remains the metadata-archive resume /
branch / compaction baseline, but it is no longer the final product session
workflow. The shipped native session tree replaces metadata-only product resume
with a Pi-like private native session tree that stores raw conversation history
for `/tree`, `/resume`, `/fork`, `/clone`, durable compaction replay, and branch
summaries. Raw transcript import from external agents remains deferred.

## Maintenance Notes

- Remove completed slices from `Next Slice` or `Near Term` in the same change
  that implements them; the git log is the authoritative record of shipped work.
- Keep deferred items here brief; put detailed design and rationale in
  `docs/harness-spec.md`.
- Keep archive and privacy rules aligned with `docs/session-storage.md`.
