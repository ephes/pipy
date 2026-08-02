# Changelog

All notable changes to pipy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); `/changelog` renders these
entries oldest-first, and a version bump shows the new entries at startup.

## [Unreleased]

### Added

- Python extension tools can now activate additional registered tools during
  execution with `ctx.set_active_tools(...)`. Purely additive changes persist a
  provider-agnostic load-point marker on that tool result; supported first-party
  Anthropic Claude 4.5+ requests keep the new definitions out of the immediate
  cache prefix with `defer_loading: true` and load them at the result with
  `tool_reference`, while supported OpenAI Responses and OpenAI Codex Responses
  models now load them at the same durable result with completed client
  `tool_search_call`/`tool_search_output` items. Older/custom-disabled models
  and removals safely send the complete current tool list. Kimi Chat
  Completions deferred tools remain a separate follow-on.
- Python extensions can now register Pi-shaped durable entry renderers with
  `api.register_entry_renderer(...)`. `ctx.append_entry(...)` records receive a
  live product-TUI component with full stored-entry metadata plus current
  expanded/width/theme context; startup replay, `/resume`, expansion changes,
  and `/reload` use the same independent registry. Entry renderers stay inert
  in print/JSON/RPC modes, and their output never enters session JSONL,
  provider context, protocol stdout, or the summary-safe archive.
- Python extensions can now observe Pi's payload-free `agent_settled`
  lifecycle hook once a provider/tool run is truly idle. Automatic retry,
  compaction work, and queued steering/follow-up/extension prompts finish
  first; unexpected mid-run failures still settle, and a settled handler may
  schedule a new run without blocking on stdin. JSON/RPC protocol events remain
  mode-owned, so their streams still emit exactly one `agent_settled`.
- Python extensions can now register Pi-shaped `before_provider_headers`
  handlers. Each real HTTP provider request exposes a mutable header map after
  request-scoped assembly; handlers run serially, may add/override values or set
  one to `None` to delete it, and fail soft. Bedrock mutations occur before
  SigV4 signing, while OpenAI Codex retries and WebSocket-to-SSE fallback reuse
  one transformed snapshot without re-firing the hook. Header data remains
  live-only and never enters session archives or JSON/RPC protocol output.
- Project-trust extensions now ship end to end. Before an unresolved decision,
  pipy activates only global and explicit CLI extensions and runs their
  `project_trust` handlers serially; `undecided` continues, the first yes/no
  wins, failures warn and continue, and only exact `remember=True` persists the
  exact cwd. Headless UI choices are inert and notifications remain stderr-only.
  The same activation instances feed provider-catalog construction and the
  initial live session, so module top-level code runs once while project and
  project-package extensions remain gated. Normal extension contexts expose
  zero-argument `is_project_trusted()` and `isProjectTrusted()` run-local reads.
- RPC mode now implements Pi's read-only `get_entries` (including optional
  `since` slicing) and `get_tree` session-inspection commands, bringing pipy's
  green RPC baseline to all 31 Pi command types. Both return a coherent session
  entries/tree and leaf snapshot; deep linear trees use depth-safe iterative
  serialization.
- RPC mode now emits Pi's payload-free `agent_settled` event after the final
  `agent_end` when the session reaches true idle. Queued steer/follow-up runs do
  not emit a premature settled event between runs.
- `--mode json` now also emits Pi's payload-free `agent_settled` as the final
  event after the run's `agent_end`. The one-shot json driver settles into idle
  when the run returns, matching Pi's `_runAgentPrompt` `finally` that `--mode
  json` forwards. The extension-surface hook now ships independently without
  duplicating this protocol event.
- `openai-codex/gpt-5.6-sol` is now a built-in Codex model (372K context, image
  input) with a seventh thinking level, `max`. The thinking vocabulary is now
  `off|minimal|low|medium|high|xhigh|max` across the CLI `--thinking`/`:level`
  suffix, settings, extension controls, RPC, and `models.json`. Shift+Tab
  cycling is model-aware: every reasoning model cycles the ordinary tier and
  appends `xhigh`/`max` only when the active model maps them, so Sol cycles all
  seven levels. The Codex request now clamps an unsupported stored level to the
  nearest supported one and emits it as `reasoning.effort` (matching Pi's
  per-request `clampThinkingLevel`); e.g. a stored `max` sends `effort: "max"`
  on Sol and clamps to `xhigh` on GPT-5.5. Sol renders a `372k` status budget.
  GPT-5.5 remains the Codex default; no bare `gpt-5.6` alias is added.
- Extension UI editor text helpers now expose Pi-canonical camelCase aliases:
  `ctx.ui.getEditorText()`, `ctx.ui.setEditorText(text)`, and
  `ctx.ui.pasteToEditor(text)`. The existing snake_case helpers remain
  available as Python convenience aliases.
- Rich extension custom-message renderers now refresh their existing TUI block when the live tool-output expanded flag changes, matching Pi's `MessageRenderer(..., { expanded })` behavior without persisting rendered rows.
- Live custom editor components now receive Pi-shaped app-action delegation:
  keybinding specs, model/thinking/tool/follow-up handlers, external-editor
  (`app.editor.external`) handoff, Escape/Ctrl-D and paste-image callbacks,
  draft preservation, and Ctrl-C remains on the terminal interrupt path. The
  built-in editor now also opens `$VISUAL`/`$EDITOR` from the resolved
  `app.editor.external` binding, default Ctrl-G, as an undoable edit. The
  default Ctrl-G editor binding is reserved from extension shortcuts; extensions
  that still register Ctrl-G now fail activation as a reserved shortcut.
- Project-trust core now gates project `.pipy` settings and resources before
  startup loads them. Decisions live in owner-private `<config>/trust.json`
  with closest-ancestor lookup; global-only `defaultProjectTrust` accepts
  `ask|always|never`; and `--approve`/`-a` plus `--no-approve`/`-na` override
  one run without persistence (the last flag wins). Untrusted runs retain
  global resources/packages and explicit CLI sources, keep `AGENTS.md`/`pipy.md`
  context behavior, and exclude project settings, extensions, skills,
  templates, commands, system-prompt files, and project package declarations.
  Print/JSON/RPC/help/list-model paths fail closed without trust prompts or
  protocol output.
- Interactive project trust now ships end to end. An unresolved interactive TTY
  opens Pi's five-choice startup selector (current folder, parent, session-only,
  decline, or session-only decline); `/trust` shows saved/inherited and current
  state and persists a next-restart decision without hot-loading resources; and
  `/reload` narrowly saves trust when a previously resource-free trusted run
  explicitly loads a newly created protected input. `/settings` now controls the
  global `defaultProjectTrust` enum. `install`, `remove`/`uninstall`, `list`, and
  `config` accept command-local `--approve`/`--no-approve`; untrusted listings
  omit project entries, global operations remain usable, and local writes fail
  before mutation. Package `update` realignment remains separate.


### Removed

- The pipy-only model-visible `truncate` tool has been removed outright, with
  no alias, compatibility path, or deprecation shim. Read excerpts, `bash`
  output, provider-visible tool results, and rendered previews retain their
  independent automatic bounds.
- The no-tool REPL has been retired. There is now one product REPL — the
  model-visible tool-loop session. The `--repl-mode` flag and the no-tool
  commands `/read`, `/ask-file`, `/propose-file`, and `/apply-proposal` (and
  their archive-side observation/patch-proposal events) are gone; the model uses
  `read`/`edit`/`write`/`bash` directly.
- The `--native-output json` one-shot flag has been removed. Automation callers
  use `pipy repl --mode json` (the full Pi-shaped session event stream) or
  `--print`/`-p` for final-text output; the removed flag now prints guidance
  naming `--mode json`. `pipy run` keeps its default human/exit-code behavior.
- The `--archive-transcript` transcript sidecar has been removed, along with the
  `pipy-session export` `--export-transcript`/`--include-transcript` reader (the
  export schema is bumped v1→v2). The native session tree is the transcript; use
  `/export` (or `pipy --export`). The removed flag prints guidance.
- The pipy-only `/template` wrapper command has been removed. Prompt templates
  are now invokable as their own `/<template-name>` slash commands (matching Pi,
  which has no literal `/template`).
- The pipy-only `/clear`, `/status`, `/help`, and `/theme` slash commands have
  been removed outright, with no deprecation aliases or notices. Pi has none of
  them; use the Pi equivalents `/new`, `/session`, and `/hotkeys`, and select a
  theme from the `/settings` dialog. This follows pipy's no-deprecation policy
  (no users yet, private until Pi parity — see `AGENTS.md`); the brief
  `/clear`→`/new`/`/status`→`/session` deprecated aliases and the `/help`→
  `/hotkeys` alias introduced earlier in this cycle are gone. The
  `--theme`/`--no-themes` load flags and `PIPY_THEME` are unchanged.

### Changed

- Custom commands, prompt templates, and extension commands can no longer be
  advertised in slash discovery or registered by an extension when their name
  collides with any built-in command. The reserved-name set now covers every
  built-in (`reload`, `tree`, `new`, `fork`, `session`, `compact`, `export`,
  `import`, `clone`, `resume`, `name`, `share`, `trust`, `scoped-models`,
  `hotkeys`, `changelog`, and the previously covered names) rather than a
  hand-maintained subset, closing an advertising gap where such a colliding
  name was still shown even though the built-in always ran instead. Runtime
  command dispatch is unchanged.
- Provider requests now carry an exact request-local advertised-tool snapshot.
  Serial `before_provider_request` tool transforms can only narrow the current
  detached definitions in their prior order, while `ctx.set_active_tools(...)`
  changes later provider iterations. Request construction and extension
  transforms route through a focused product adapter; public request formats
  and callback ordering are unchanged.
- Model-driven tool definition lookup, execution, and policy-error creation now
  flow through the synchronous canonical `AgentToolCapabilities` port. Product
  registry composition, CLI/run filters, active-tool changes, extension reload,
  workspace context, and executor construction live behind
  `NativeToolCapabilities`; scheduling remains sequential and public formats,
  extension ordering, persistence, and archive privacy are unchanged.
- Canonical agent-history compaction now lives in the dependency-neutral
  `native.agent.history` layer. The obsolete mixed
  `native.session_compaction` module and its unused no-tool compaction path are
  removed; product trigger policy, exact summary text, extension ordering, and
  durable session-tree writes remain owned by the native tool-loop session.
- Extension custom footers now receive live product-TUI `FooterData.onBranchChange(...)` callbacks that rebuild/repaint the footer on git branch changes; headless snapshots keep the safe no-op disposer.
- The native `google-generative-ai` provider now injects Pi's per-model
  `generationConfig.thinkingConfig`: a `thinkingLevel` enum (Gemini 3 Pro/Flash,
  Gemma 4) or a `thinkingBudget` token count (Gemini 2.5 family) with
  `includeThoughts: true` when thinking is on, and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset — matching Pi's `google.ts` `streamSimpleGoogle`/`buildParams`.
  Non-reasoning models still omit `thinkingConfig` entirely.
- The native `google-vertex` provider now injects Pi's per-model
  `generationConfig.thinkingConfig` from `google-vertex.ts` (its
  `THINKING_LEVEL_MAP` variant) in both Express (api-key) and ADC (bearer) modes:
  a `thinkingLevel` enum for Gemini 3 Pro/Flash, a `thinkingBudget` token count
  otherwise (`includeThoughts: true` when on), and a per-model disabled config
  (no `includeThoughts`) when a reasoning-capable model runs with thinking
  off/unset. Catalog construction forwards the resolved
  `reasoning_effort`/`thinking_disabled`. It deliberately diverges from
  `google-generative-ai`: **no** `2.5-flash-lite` budget table (flash-lite falls
  into the `2.5-flash` branch → minimal `128`, not `512`) and **no** Gemma 4
  special-casing (Gemma is not a Vertex Gemini model).
- The native `azure-openai` provider now resolves its endpoint and deployment
  from Pi's config-source env vars: `AZURE_OPENAI_BASE_URL` (the base URL),
  `AZURE_OPENAI_RESOURCE_NAME` (used to build a default
  `https://{name}.openai.azure.com/openai/v1` base when no base URL is set),
  `AZURE_OPENAI_DEPLOYMENT_NAME_MAP` (a `modelId=deployment,...` map overriding
  the deployment name per model id), and the existing `AZURE_OPENAI_API_VERSION`
  — matching Pi's `resolveAzureConfig`/`resolveDeploymentName` precedence. The
  pipy-only `AZURE_OPENAI_ENDPOINT` env name was dropped for parity; provider
  availability now requires `AZURE_OPENAI_API_KEY` plus one of
  `AZURE_OPENAI_BASE_URL`/`AZURE_OPENAI_RESOURCE_NAME`.
- The native `anthropic-messages` provider now emits Pi's explicit
  `thinking: {type: "disabled"}` when a reasoning-capable Claude model runs with
  thinking off/unset, instead of omitting the `thinking` key — matching Pi's
  product path (`streamSimpleAnthropic` → `buildParams` `thinkingEnabled ===
  false`). Non-reasoning models still omit `thinking` entirely, and the
  `amazon-bedrock` adapter is unchanged (Pi omits thinking fields there rather
  than sending a disabled shape).
- Bare `pipy` and `pipy "<prompt>"` now launch the interactive product session
  like Pi (a bare positional prompt seeds the first message), while
  `auth`/`run`/`repl`/`config`/`install`/... stay reachable as subcommands. A
  bare token equal to a subcommand name dispatches that subcommand; quote it via
  `pipy repl "<word>"` or `pipy -p "<word>"` to send it as a prompt instead.

### Fixed

- Concurrent extension coding-session controls now serialize complete provider,
  durable session-tree, custom-render, and queued-input effects. Native tree
  id/parent selection, in-memory publication, labels/names, RPC snapshots, and
  JSONL append order now share one guard, as do all coding-input check/use paths.
  Terminal shutdown waits for effects accepted before close, then detaches the
  live generation and closes its outboxes/chrome exactly once; later completion,
  custom-entry, name, label, and custom-message calls raise
  `ExtensionCapabilityError` without changing provider/session/input state,
  while read-only final-tree views remain available.
- A live extension message can no longer be erased when
  `api.send_user_message(...)` or `api.send_message(...)` races the session's
  outbox drain. Live append and detach now serialize; rejected or retired queue
  handles silently keep the existing `None` return and accumulate nothing.
- Extension reload no longer clears live retained TUI chrome before activation
  and dynamic-flag validation succeed. Invalid flags or another rejected
  candidate now preserve the prior title, header/footer/widgets, indicator,
  terminal-input listeners, autocomplete providers, editor component, and
  hidden-thinking label; rejected candidate chrome never paints and no longer
  re-fires the retained generation's `session_start`, which previously appended
  duplicate registrations and rebuilt its editor. A reload now invokes exactly
  one replacement-generation `session_start` against the candidate before
  acceptance and before accepted staged custom messages become visible, then
  publishes one coherent flags/tools/renderers/hooks/providers/menu/chrome
  generation. The publication gate remains active through
  accepted staged delivery, two-phase route release, and gate drain—even while
  those paths invoke extension-visible sinks after the session commit unlocks—
  before chrome reconciliation. Retained active-tool and thinking controls now
  stay bound to their creating generation: stale, publication-pending, and
  post-run calls return `False` without changing tool visibility, thinking
  state, the session tree, persisted JSONL, or the footer. Thinking commits and
  durable entries also preserve one order under concurrent callers. Retained
  model controls now use the same generation, publication-gate, and terminal
  refusal boundary: provider/catalog construction is prepared outside the
  session mutex, the selection plus coding provider/history/usage commit is
  atomic under it, and footer/default persistence follows after unlock. A stale,
  gated, terminal, failed-construction, or superseded prepared model returns
  `False` without rebinding or persisting; a callable first released after
  teardown cannot construct a provider. If lifecycle, provider/catalog, or
  final chrome preparation then refuses the reload,
  non-staged, non-chrome lifecycle effects such as `notify` may already have
  occurred; candidate chrome is discarded and all candidate staged messages
  that the earlier reload path could expose are suppressed. This is part of the
  `session_start`-before-acceptance ordering change, not another behavior delta.
  Registrations it does not rebuild
  (including autocomplete, a custom editor, and the hidden-thinking label) are
  cleared, while custom-editor text returns to the built-in editor. Late
  retained writes to a rejected candidate's closed sink are ignored with their
  prior `None` return shape, while `on_terminal_input()` returns an inert
  disposer. Concurrent owner rotation during candidate-host publication now
  refuses before any generation/provider/tool/renderer write, preserving the
  newer live state while retiring the terminal candidate route and closing its
  chrome. Concurrent retained writes still cross accepted chrome reconciliation
  through a short effect-free ownership handoff; post-publication interrupts
  propagate after that reconciliation leaves the replacement explicitly live,
  without double close. Ordinary retained UI contexts now stay bound to their
  originating chrome handle, so writes through a retired handle are ignored
  instead of retargeting the replacement; terminal teardown invocation remains
  a separate follow-on.
- Extension activation APIs retained after successful activation or candidate
  rejection can no longer appear to register commands, shortcuts, hooks, tools,
  providers, flags, or renderers into dead candidate state. Late contribution
  registration now raises `ExtensionCapabilityError` at the call boundary,
  `(str, Enum)` names and hostile `str.__str__` overrides retain their exact
  underlying registration and `get_flag(...)` lookup value, and messages racing
  after the activation seal
  can no longer leak into (or mutate) the accepted frozen activation snapshot.
  Unexpected extension-controlled normalization/copy exceptions now fail closed
  to the registration family's bounded invalid reason and type-only diagnostic;
  the first failure remains recorded even when extension code catches the raised
  validation error, so this hostile case does not preserve exact pre-R1 reason
  behavior. Provider-only catalog scans now terminally finalize accepted hosts:
  staging, sends, and publication are inert, while guarded registration-time
  default flag reads stay available to detached provider factories that captured
  `api`; the catalog helper does not parse/apply CLI tokens, while live session
  activation still exposes parsed overrides. Rejected or abandoned activation
  disposal still clears flag values.
- Calling `unregister_provider(...)` with a non-string, empty/whitespace-only,
  or slash-containing provider name now records an `invalid_provider` activation
  failure and disables that extension, instead of silently staging or ignoring
  the malformed unregistration request.
- Escaped adapter exceptions no longer copy raw exception messages into the
  durable metadata archive or its Markdown summary. Those records retain only
  the bounded exception type and fixed lifecycle metadata, while the in-memory
  `RunResult` keeps its existing caller-facing failure detail.
- Registered tools omitted from the exact provider request can no longer reach
  extension tool hooks or execution when a provider returns them anyway. They
  now produce the normal balanced, budget-consuming `unknown tool` result with
  provider correlation and pipy request identities intact, without incrementing
  malformed-call or real-invocation counters or invoking extension custom
  renderers. A tool activated by an earlier call is not authorized later in the
  same provider response.
- Extension `deliverAs=nextTurn` custom context is now an identity-anchored,
  request-only overlay for exactly the next accepted run. It remains visible
  through every provider/tool iteration without entering canonical history or
  run results, hook prompt transforms cannot confuse it with equal text, and
  automatic compaction continues safely on durable history during the run.
- Valid model-selected tool executions that return error observations (for
  example read failures or timed-out shell commands) no longer count as
  malformed provider tool calls, so the product tool loop keeps feeding those
  observations back to the model instead of aborting after three such errors.
- Terminal color detection now uses truecolor RGB styling only when the
  terminal explicitly advertises it (`COLORTERM=truecolor`/`24bit` or a
  direct-color `TERM`), so ordinary `*-256color` sessions use Pi's 256-color
  fallback palette instead of displaying wrong chrome colors.
- Slash/local commands such as `/quit` now remain editable and submittable while
  a `!` shell shortcut or model-driven bash tool is streaming output, so
  long-running tests no longer trap the user in the product TUI.
- Raw terminal input now preserves UTF-8 prompt text in the product TUI and
  slash-menu editor, so non-ASCII characters such as `ö` no longer render as
  replacement characters or reach the provider corrupted.
- The interactive TUI now paints edge-to-edge at the true terminal width,
  matching Pi, removing the blank right-hand column. Full-row elements — the
  user-message and tool/bash background bands, the input-frame separators, and
  the bottom status line — now reach the final column instead of stopping one
  short. The input line keeps its one-column cursor-safety margin internally, so
  the hardware cursor still never lands in the last column.

### Changed

- Product TUI long editable prompts now soft-wrap inside the input frame instead
  of horizontally scrolling in one row. Cursor movement maps across wrapped
  rows, footer/status rows stay pinned, and long typed/pasted input plus resize
  are covered by real-PTY tests at 80x24 and 100x40.
- The pipy-only metadata-only `--resume RECORD` / `--branch LABEL` repl flags
  are retired: the native session tree is the product session source. The
  separate `pipy-session resume-info` archive utility is unchanged.

### Added

- Provider/model user documentation now covers listing models, provider/model
  selection, credentials, `models.json`, ds4, thinking/images metadata, and
  current provider follow-ons.
- Settings and keybindings user documentation now covers pipy's global/project
  settings files, reload workflow, common Pi-shaped fields and pipy
  divergences, key syntax, namespaced action ids, defaults, and customization
  examples.
- Session and compaction user documentation now covers the native product
  session tree, startup flags, `/session`/`/resume`/`/tree`/`/fork`/`/clone`,
  durable compaction, export/share pointers, and the separate `pipy-session`
  metadata/catalog utility.
- feat(extension-api): editor text helpers for command/shortcut contexts.
  Extensions can read the core prompt buffer, replace it, or paste literal text
  at the current cursor via `ctx.ui.get_editor_text()`,
  `ctx.ui.set_editor_text(text)`, and `ctx.ui.paste_to_editor(text)`. Headless
  reads return `""`; headless writes and pastes no-op deterministically.
- feat(extension-api): theme controls for command/shortcut contexts (rich-UI
  item E). Extensions can read and switch the chrome theme via `ctx.ui.theme`
  (the active `ChromePalette`), `ctx.ui.get_all_themes()` (`{"name", "path"}`
  per available theme, default first), `ctx.ui.get_theme(name)` (load a palette
  without switching; `None` when unknown), and
  `ctx.ui.set_theme(name_or_palette)` (`{"success", "error"}`), mirroring Pi's
  `theme`/`getAllThemes`/`getTheme`/`setTheme`. Reads are ambient (the global
  package theme registry plus `PIPY_THEME`/the chrome store) and work
  deterministically even headless; `set_theme` requires a live UI and returns
  `{"success": False, "error": "UI not available"}` headless without mutating
  process state, while a live call reuses the `/settings` `select_theme`
  mechanism so the next frame repaints. `get_all_themes()` keeps Pi's
  `{name, path}` shape but `path` is always `None` (the session theme registry
  retains only `name -> palette`; package theme file paths are not exposed to
  extension code).
- feat(extension-api): rich message renderers — slice C. A
  `register_message_renderer` renderer may now return a themed component via a
  required second `(data, ctx)` parameter (a `MessageRenderContext` with
  `custom_type`/`data`/`expanded`/`width`/`theme`); the component is committed
  SGR-preserving with no forced `[custom_type]` label, render-once at the
  append-time width, and fail-soft to the plain path. A 1-arg `renderer(data)`
  (including the capture-default idiom) keeps its existing plain-text behavior;
  the context parameter must be required (`def render(data, ctx)`, not
  `ctx=None`). The rendered body is live-only and never archived. Active-branch
  custom entries now replay into startup-opened TUI sessions, including
  `--session`/`--continue`/`--resume-session` opens, without mutating the
  session file. `ctx.send_message` / persisted `CustomMessageEntry` values
  now render through registered message renderers when present, receiving a
  Pi-shaped payload with `customType`, `content`, `display`, and `details`,
  while no-renderer cases continue to display stored content. Deferred:
  streaming `deliverAs`/`triggerTurn` follow-ons beyond the shipped idle and
  queued paths.
- feat(extension-api): persistent chrome widgets (set_widget/set_header/
  set_footer/set_title/set_working_indicator) — slice B. Extensions can pin an
  above/below-editor widget, an exclusive custom header and footer (with git
  branch via `FooterData`), the terminal title, and a custom working indicator;
  chrome re-renders width-reactively, falls back fail-soft, disposes components
  on replace/clear/reload, and renders live from `session_start` in an
  interactive TTY.
- Extensions can render their own tool call/result rows (`render_call`/
  `render_result`) with themed color.
- Discovered skills are now advertised in the tool-loop system prompt when the
  `read` tool is available, matching Pi's skill model: each skill contributes an
  `<available_skills>` entry (name, description, and absolute location), and the
  model loads a skill's body on demand via the `read` tool. Each skill's parent
  directory is added to the read-only reference roots so the model can read skill
  bodies (including global skills outside the workspace). The `/skill` command is
  kept, and the archive-safe skill metadata (path label, sha256, byte length,
  truncated, name) is unchanged.
- Theme selection now lives in the `/settings` dialog as a theme row + picker
  (matching Pi, which has no `/theme` command); the chosen theme persists through
  settings and re-colors the chrome on the next frame.
- Python extensions can now register custom session-entry renderers with
  `api.register_message_renderer(...)`; command and shortcut handlers can call
  `ctx.append_entry(...)` to persist JSON-safe `custom` entries in the native
  product session tree and render them in the product TUI or captured-stream
  diagnostics without starting a provider turn.
- Python extension command/shortcut contexts now expose simple Pi-shaped UI
  primitives: `ctx.ui.select`, `ctx.ui.input`, `ctx.ui.confirm`,
  `ctx.ui.set_status`, `ctx.ui.set_working_message`, and
  `ctx.ui.set_working_visible`. Interactive product-TUI runs use simple
  overlays, live status rows, and sticky provider-turn working controls;
  headless runs return cancel/default values without blocking.
- Python extensions can now register dynamic `pipy repl` tool-loop CLI flags
  with `ExtensionFlag`; parsed values are available to extension commands,
  shortcuts, hooks, and tools through `ctx.flags`.
- Python extensions can now participate in live product-session operations:
  `user_bash` hooks may block, rewrite, exclude, or synthesize `!`/`!!` shell
  shortcut results; `before_provider_request` hooks may transform bounded
  provider request fields and narrow model-visible tools for the current
  request; `session_before_switch`, `session_before_fork`,
  `session_before_compact`, and `session_before_tree` hooks may gate stateful
  session operations; and safe command/shortcut/pre-turn contexts expose
  `ctx.set_active_tools(...)`, `ctx.set_model(...)`, and
  `ctx.set_thinking_level(...)` through the native provider/session/tool
  boundaries. The new live-session parity gate is
  `scripts/parity_checks/extension_live_session_conformance.py --json`.
- Pi-shaped per-run source-loading flags for `pipy repl`: `--extension`/`-e`,
  `--no-extensions`/`-ne`, `--skill`, `--no-skills`/`-ns`,
  `--prompt-template`, `--no-prompt-templates`/`-np`, `--theme`, and
  `--no-themes`. Explicit CLI paths are temporary session sources that load
  before workspace/global/package defaults, survive matching `--no-*`
  discovery cutoffs, and override persisted `+/-pattern` resource filters while
  keeping `enable_skill_commands=false` as a hard skill-command disable.
- Native product export/import/share and self-update planning:
  - `/export` writes a self-contained HTML export of the full native session
    tree; `/export <path.jsonl>` writes the active branch as a linearly
    re-chained portable JSONL file.
  - `/import <path.jsonl>` copies a portable JSONL file into the native session
    store and resumes it after confirmation; `--yes` is accepted for
    noninteractive command scripts.
  - `pipy --export <session.jsonl> [output.html]` exports an existing native
    session file to HTML and exits.
  - `/share` uploads the HTML export as a secret GitHub gist through a stdlib
    GitHub API boundary using `GITHUB_TOKEN`/`GH_TOKEN` or `gh auth token`.
  - `pipy update self|pipy [--force] [--dry-run]` plans install-method-aware
    self-update commands for `uv tool`, `pipx`, `pip`, and user `pip`, while
    unknown/development installs and unconfigured package names fail safe with
    manual instructions.
  - New gate:
    `scripts/parity_checks/export_distribution_conformance.py --json`.
- Pi-style extension **package manager CLI** for local-path and managed git
  package sources ([docs/extension-api.md](docs/extension-api.md)): `pipy
  install <source> [-l]`, `pipy remove`/`pipy uninstall <source> [-l]`, and
  `pipy list` record and report package sources in a `packages` array in user
  `<config>/settings.json` or project `<cwd>/.pipy/settings.json` (with `-l`),
  preserving object-form `{source, ...}` entries. Supported git sources clone
  into pipy's managed package cache (`<config>/git` for user scope,
  `<cwd>/.pipy/git` for project scope), `pipy update --extensions`, `pipy
  update --extension <source>`, `pipy update <source>`, and bare `pipy update`
  refresh managed git packages through bounded fetch/reset, and local-path
  package updates are skipped as no-ops. `pipy config <enable|disable>
  <skill|prompt|theme|extension> <name>` writes Pi-shaped `+pattern`/`-pattern`
  resource filters without deleting discovered resources. PyPI/npm,
  `git+...`, credentialed URL userinfo, and ambiguous unsupported remote
  schemes fail closed; a missing path fails closed, removing an unconfigured
  source exits non-zero, a corrupt settings file is never overwritten, and no
  package lifecycle scripts run.
- Pi-style extension **package runtime composition**: installed local-path and
  managed git packages now contribute skills, prompts, themes, and Python
  extensions to a session through discovery
  ([docs/extension-api.md](docs/extension-api.md)). A package declares its
  resources in an optional `pipy-package.toml [resources]` table (mapping Pi's
  `pi.{extensions,skills,prompts,themes}`) or via convention subdirectories.
  Contributed resources are discovered at lowest precedence (a workspace/global
  resource wins a name collision), are name-deduped first-wins, and honor both
  the global `pipy config` `+pattern`/`-pattern` filters and a package's own
  object-form `{source, skills, prompts, themes}` filters. Runtime startup never
  clones or fetches git sources; it only reads already installed cache paths and
  preserves user/project cache scope when resolving configured git packages.
  This adds file-based chrome themes: a package theme `.toml` becomes
  selectable with `/theme <name>` and re-colors the chrome. `pipy config` lists
  package-contributed resources, and `/reload` re-discovers them. Package source
  paths and resource bodies never enter the default metadata archive. Remote
  PyPI/npm package installation remains deferred pending a broader supply-chain
  policy. See the example package `docs/examples/packages/demo-pack/`.
- Pi-style session startup flags and an interactive session picker for the
  native product session tree ([docs/session-tree.md](docs/session-tree.md)):
  - new startup flags `--session-id <id>` (open the native session with this
    exact id, or create one carrying it), `--session-dir <dir>` (native session
    store root override — the separate `$PIPY_SESSION_DIR` metadata-archive root
    is never reused for it), and `-n`/`--name <name>` (name the session at
    startup), alongside the existing `-c`/`-r`/`--session`/`--fork`/
    `--no-session`.
  - Pi mutual-exclusion errors: `--fork` and `--session-id` each conflict with
    `--session`/`--continue`/`--resume-session`/`--no-session`.
  - cross-project `--session <partial-id>`: a partial id that matches only a
    session in a different project prompts to fork it into the current
    workspace, aborting cleanly if declined.
  - `/resume` opens an interactive picker overlay on a TTY — type to search,
    `Tab` toggles current-project/all-projects scope, `Ctrl+P` the path column,
    `Ctrl+S` the sort, `Ctrl+N` named-only, `Ctrl+R` renames, `Ctrl+X` deletes
    after a `[y/N]` confirmation (the active session is protected), Enter opens,
    `Esc`/`Ctrl+C`/`Ctrl+D` cancel. It renders inline (no alternate screen),
    repaints on resize, runs no provider turn, and sanitizes user-controlled
    names/paths against terminal escape injection. `-r` opens the same picker at
    startup on a TTY; a non-TTY stream keeps the deterministic listing plus the
    `named`/`rename`/`delete --yes` subcommands and continues the most recent
    session.
  - a Pi comparison gate (`scripts/parity_checks/session_tree_pi_comparison.py
    --json`) runs the canonical tree workflow against Pi's real `SessionManager`
    and asserts matching name, branch/leaf chains, fork semantics, and durable
    reconstruction; the extended `session_tree_conformance.py` proves the new
    flags and picker rows/actions through the product paths.
- Pi-style headless automation surfaces for the product tool loop, through
  pipy-owned stdlib boundaries with no new runtime dependency
  ([docs/automation-rpc.md](docs/automation-rpc.md)):
  - `pipy repl --mode json "<prompt>"` runs one non-interactive turn and emits
    the native session header line followed by the full Pi-shaped session event
    stream (`agent_start`/`turn_start`/`message_start`/`message_update` with a
    `text_delta` `assistantMessageEvent`/`message_end`/`turn_end`/`agent_end`
    and `tool_execution_*`) as strict LF-only JSONL on stdout; diagnostics stay
    on stderr. Full assistant/tool/bash content is emitted like Pi; auth
    secrets/tokens are never emitted.
  - `pipy repl --print`/`-p "<prompt>"` prints only the final assistant text to
    stdout (Pi `-p`); failures go to stderr with a non-zero exit.
  - `pipy repl --mode rpc` starts a long-lived stdin/stdout JSONL protocol with
    Pi's command names: async `prompt` (correlated success then streamed
    events); `steer`/`follow_up` (queued during an active run and delivered as
    the next run after it settles, one message per turn boundary
    steering-then-follow-up, each observable via `queue_update` and counted in
    `pendingMessageCount`) and `abort` (cancels the active
    run; queued steering for that run is discarded) — a documented pipy boundary
    over Pi's in-turn injection; `bash` (on a worker thread; `abort_bash` errors
    while a sandboxed bash is in flight rather than falsely claiming a cancel);
    `get_state`/`get_messages`/`get_session_stats`/
    `get_last_assistant_text`, `set_session_name`, and queue-mode commands;
    model/thinking commands are accepted and reflected in `get_state`/events but
    do not yet switch the live provider or thread the thinking level into the
    running provider request (a documented follow-on); and well-formed error
    responses for unimplemented commands. All 29 Pi RPC command types are
    accepted; unknown commands and unparseable lines return well-formed error
    responses, never a crash. The native session
    tree is the introspection source; events derive from the real tool-loop
    run, not a parallel model.
  - The legacy metadata-only `--native-output json` on `pipy run` is deprecated
    in favor of `--mode json`; its `--help` now points there.
  - The session event grammar matches Pi's: after `turn_start` the user
    message emits its own `message_start`/`message_end` pair before the
    assistant message begins.
  - Gated by `scripts/parity_checks/automation_rpc_conformance.py --json` and
    `tests/test_native_automation_*.py`, plus a deterministic Pi-vs-pipy
    comparison (`scripts/parity_checks/automation_pi_comparison.py --json` with
    `scripts/parity_checks/pi_faux_event_driver.mts`) that drives the real local
    Pi and pipy with offline providers and asserts matching normalized event
    order/discriminators, assistant text + delta concatenation, `agent_end`
    semantics, and durable session-tree reconstruction.
- Pi-style interactive TUI/editor workflow depth for the product tool-loop
  terminal (`pipy repl --agent pipy-native --repl-mode tool-loop`), all through
  pipy-owned stdlib boundaries with no new runtime dependency and the inline
  (no-alternate-screen) contract preserved:
  - `@` file picker with Pi exact/prefix/substring ranking (not fuzzy) over a
    bounded, `.git`/ignored-aware workspace walk, and general Tab path
    completion (prefix-match, dirs-first, `~/` expansion, space-quoting) that is
    a no-op in prose.
  - Local `!`/`!!` shell shortcuts reusing the real bash execution boundary,
    with a bash-mode input affordance, context (`!`) vs no-context (`!!`)
    recording, and Escape cancellation of a running command.
  - `Shift+Tab` thinking-level cycling (off→minimal→low→medium→high, clamped to
    model reasoning support, recorded as a `thinking_level_change` native-tree
    entry) and `Ctrl+P`/`Shift+Ctrl+P` model cycling over the scoped/available
    set.
  - `Ctrl+O` tool-output expansion and `Ctrl+T` thinking-block fold as renderer
    view flags (the thinking fold persisted to `hideThinkingBlock`).
  - Queued steering / follow-up during active turns (`Alt+Enter` follow-up,
    `Alt+Up` restore-to-editor), a pending-messages region, steering-then-
    follow-up drain order, and steering interruption via the existing cancel
    token.
  - Clipboard image paste (`Ctrl+V`, owner-only temp file under an image
    reference root) and terminal drag-drop file references; image bytes never
    reach the metadata archive.
  - A `/scoped-models` multi-select overlay defining the Ctrl+P cycle set, new
    `/settings` actionable rows (tool-output/thinking folds, thinking-level
    cycle, scoped models), and startup hints + `/hotkeys` advertising every
    binding.
  - The terminal-native mouse-selection invariant: the renderer never enables
    xterm mouse tracking, so click-drag selection over scrollback keeps working.
  - New gate `scripts/parity_checks/tui_workflow_conformance.py --json` drives
    the real product PTY path and proves all of the above (plus non-TTY
    fallbacks and archive privacy) deterministically.
- User-facing terminal setup and tmux setup docs now cover pipy's inline TUI,
  modified-key expectations, bracketed paste, file/image drops, clipboard
  behavior, scrollback, and common platform caveats.

### Fixed

- A `/…` slash command or `!…` bash shortcut submitted with Enter mid-turn now
  runs locally (matching Pi's editor `onSubmit`): it interrupts the turn and
  dispatches through the normal local-command path instead of being steered to
  the model. Only ordinary prose becomes a steering message, so the queue lanes
  hold prompt text exclusively.
- Queued steering/follow-up messages that begin with `/` or `!` (including an
  `Alt+Enter` follow-up or an RPC `steer`/`follow_up`) now reach the model
  verbatim when the queue drains.
  Previously a queued line starting with a slash-command or `!`-shell prefix was
  re-interpreted as a local command on delivery and silently dropped from the
  conversation; drained messages are provider-visible prompt text and bypass
  local-command dispatch (they still resolve any `@file`/`@image` references).
- Moving the caret (`←`/`→`/`Home`/`End`) now dismisses the `@`/path completion
  popup. Previously the popup stayed anchored to the caret offset where it
  opened, so accepting after a move spliced the candidate at a stale offset and
  duplicated/corrupted the active token; it reopens on the next edit.
- Aborting (Escape/Ctrl-C) or restoring (`Alt+Up`) while a queued turn is
  draining now brings the remaining queued prompts back to the editor. Once a
  turn settled (or steering promoted), the queue moved into an internal drain
  that the restore path ignored, so the not-yet-delivered prompts stayed hidden
  and kept auto-submitting to the model after the cancellation; they are now
  restored along with the steering/follow-up lanes.
- `Ctrl+V` clipboard-image reads are bounded and isolated: the helper's stdin is
  `/dev/null` and the read enforces a wall-clock deadline, so a misbehaving
  clipboard tool (one that hangs or never closes its output) can no longer
  freeze the editor or consume terminal keystrokes.
- Tab path completion no longer offers ignored/generated entries (e.g.
  `node_modules/`) or symlinks escaping the workspace for workspace-relative
  directories, matching the `@` picker and the read policy; explicit
  absolute/`~/` navigation the user points Tab at is still listed as-is.
- OpenAI-Codex streams now use a configurable 300-second header/body idle
  timeout by default instead of the former hard-coded 60-second socket timeout.
  Recognized connection, timeout, reset, and truncated-stream failures become
  sanitized provider failures, while deliberate cancellation remains immediate
  and non-retryable.
- OpenAI-Codex now retries bounded transient HTTP and transport failures across
  the complete request-plus-stream attempt, with cancellation-aware exponential
  backoff and capped `Retry-After` support. Retries stop before replay once any
  provider event is parsed, preventing duplicate visible text, reasoning, tool
  calls, or tool effects.
- OpenAI-Codex now honors `transport: auto|sse|websocket` with a real Responses
  WebSocket path, Pi-shaped pre-event SSE fallback, WebSocket connect timeout
  settings, auto-mode fallback memory, and no post-event fallback or replay.
- The parity runner now records child-attempt start/finish events, distinguishes
  runner timeouts from signal exits, and narrowly retries a legacy raw
  `pipy: The read operation timed out` child tail only when no branch, HEAD,
  ref, or worktree progress occurred.

## [0.1.0] - 2026-06-03

### Added

- Pi-style settings/config/keybindings system for the native runtime:
  - Layered `settings.json` (global `<config>/settings.json` on the
    `PIPY_CONFIG_HOME` → `${XDG_CONFIG_HOME}/pipy` → `~/.config/pipy` chain, plus
    project `.pipy/settings.json`) with Pi migrations, one-level deep merge with
    project precedence, CLI/env overrides, parse-error isolation, and
    field-scoped lock-guarded writes that preserve unknown keys.
  - `keybindings.json` with the default editor/app binding table (single key
    spec or array of alternatives), legacy-name migration, malformed-file
    fallback to defaults, and `/hotkeys` rendered from the resolved manager.
  - Settings drive `defaultProvider`/`defaultModel`, `theme`, `quietStartup`,
    `promptHistory.enabled`, and `autocompleteMaxVisible` at startup; `/settings`
    reports the resolved configuration.
  - System-prompt inputs: `--system-prompt`, repeatable `--append-system-prompt`,
    `SYSTEM.md` / `APPEND_SYSTEM.md` auto-discovery, and `--no-context-files`/
    `-nc`.
  - `retry.*` feeds the provider HTTP retry policy and `compaction.enabled`
    gates auto-compaction.
  - Scoped models: `enabledModels` + `/scoped-models` (view/set/clear/cycle) and
    Ctrl+P forward cycling.
  - Resource enablement via `pipy config` (`-pattern`/`+pattern` over
    `skills`/`prompts`/`themes`/`extensions`) and `enableSkillCommands`.
  - `/reload` re-reads settings, keybindings, resources, and theme.
  - `/changelog` and the `--version` surface.
- Provider/model catalog closeout for the native runtime:
  - Catalog-backed provider construction now covers the OpenAI-compatible Chat
    Completions family, implemented catalog-constructed non-completions
    families, `pipy run` one-shot construction, and startup
    `--native-provider`/`--native-model` resolution through the shared resolver.
  - Extension-registered providers now contribute temporary per-run catalog
    rows: they appear in `--list-models`, resolve at startup when the extension
    is loaded, switch via `/model`, recompute on `/reload`, and construct
    through the extension `ProviderPort` factory without persisting package or
    catalog state.
  - The provider catalog conformance gate covers Verification-Plan items 1-25
    with deterministic fake HTTP/product-path checks and no network access.
- True active-turn provider-request cancellation for the native tool loop:
  Escape and Ctrl-C each thread a per-turn `CancelToken`
  (`pipy_harness.native.cancellation`) into `ProviderPort.complete(...)` that
  shuts the live `urllib`/SSE connection down — during the header wait or the
  body/stream read — so the worker's blocking read raises
  `ProviderCancelledError` instead of finishing the request; the worker is then
  best-effort joined and the loop renders Pi-style red `Operation aborted`
  without appending an assistant/tool observation. The socket-shutdown read
  path tolerates the `http.client` `_close_conn` shutdown race (a concurrent
  `fp = None` surfacing as `AttributeError`) by mapping it to cancellation only
  when the token is cancelled, so an aborted body read cannot leak a spurious
  provider error.
- Python SDK/headless embedding documentation for `pipy_harness.sdk`, including
  the current one-shot in-process surface, fake-provider default, current limits,
  and relationship to planned JSON/RPC automation.
