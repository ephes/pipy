# Pi-Mono Gap Audit

Status: comparison snapshot and implementation specification, originally
written 2026-06-02 and groomed 2026-06-17 after session CLI/pickers,
settings/keybindings, TUI workflow, provider-catalog construction, JSON/RPC
automation, and extension/package slice-12 runtime composition shipped. The
extension follow-on section was refreshed 2026-06-20 after extension
live-session hooks, dynamic tool-loop flags, simple `ctx.ui` primitives, and
the first custom session-entry/message-rendering slice shipped.

This audit compares pipy's current docs/specs and command help with the local Pi
reference in `/Users/jochen/src/pi-mono` plus the installed `pi 0.78.0` help.
It is a slice-selection aid: the detailed behavioral specs remain the source of
truth for each topic, but this page records the biggest remaining gaps in one
place and states what pipy should implement next.

## Sources checked

Pipy docs/specs:

- `docs/parity-plan.md`, `docs/backlog.md`, `docs/pi-parity.md`,
  `docs/parity-criterion.md`
- `docs/provider-catalog.md`, `docs/settings-config.md`,
  `docs/tui-workflow.md`, `docs/automation-rpc.md`,
  `docs/export-distribution.md`, `docs/extension-api.md`,
  `docs/session-tree.md`, `docs/user-documentation.md`
- current `uv run pipy --help`, `uv run pipy run --help`,
  `uv run pipy repl --help`
- current provider-catalog gate:
  `uv run python scripts/parity_checks/provider_catalog_conformance.py --json`

Pi reference:

- `packages/coding-agent/src/core/slash-commands.ts`
- `packages/coding-agent/src/cli/args.ts`
- `packages/coding-agent/src/core/model-registry.ts`,
  `model-resolver.ts`, `auth-storage.ts`, `settings-manager.ts`,
  `keybindings.ts`, `resource-loader.ts`, `package-manager.ts`
- `packages/coding-agent/src/package-manager-cli.ts`
- `packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `packages/coding-agent/src/modes/print-mode.ts`,
  `modes/rpc/rpc-types.ts`, `modes/rpc/rpc-mode.ts`
- `packages/coding-agent/src/core/export-html/*`
- `packages/coding-agent/src/core/extensions/*`
- `packages/ai/src/models*.ts`, `oauth.ts`, `env-api-keys.ts`, and provider
  implementations
- installed `pi --help`

## Fresh command-surface deltas from this grooming pass

The direct help comparison remains the fastest sanity check for parity drift.
After the 2026-06-17 grooming pass, the important command-surface deltas are:

- `pi --help` is a single top-level product command with interactive, print,
  JSON, RPC, session, provider/model, settings/resource, package-management,
  export, and update flags. `uv run pipy --help` is now Pi-shaped: bare `pipy`
  and `pipy "<prompt>"` launch the interactive session and the help text reads
  as a single product command, with `auth|run|repl|config|install|...` kept as
  secondary subcommands (a bare subcommand-name token is a documented
  reserved-word exception). The top-level compatibility dispatch shipped in the
  2026-06-20 cleanup.
- Pi top-level package commands are partially present in pipy: `install`,
  `remove`/`uninstall`, `list`, and `config` now manage local-path and managed
  git package sources plus resource filters; installed packages contribute
  extensions/skills/prompts/themes through discovery. Package `update` refreshes
  managed git caches. PyPI/`npm:` sources remain deferred to a broader
  supply-chain policy.
- Pi automation modes (`--mode json`, `--mode rpc`, `--print`/`-p`) ship in
  pipy under the REPL product path and are backed by the native session tree.
  The old metadata-only `--native-output json` has been **removed** (2026-06-20);
  callers use `--mode json`, and the removed flag emits guidance naming it.
- Pi session flags and picker workflows now ship: `--session-id`,
  `--session-dir`, `--name/-n`, `-c`, `-r`, `--session`, `--fork`, and
  `--no-session`, with Pi-style mutual exclusion and the cross-project fork
  prompt.
- Pi resource/settings flags mostly ship on pipy's current product surface:
  system-prompt replace/append, `--no-context-files`, `--version`, resource
  enablement through `pipy config`, per-run source-loading flags
  (`--extension`, `--skill`, `--prompt-template`, `--theme`, and matching
  `--no-*` cutoffs), `/reload`, `/hotkeys`, `/scoped-models`, `/changelog`,
  settings/keybindings files, and scoped model cycling. The resource-wrapper
  cleanup landed (2026-06-20): the `/template` wrapper was dropped (templates are
  `/<name>`); `/clear`, `/status`, `/help`, and `/theme` were removed outright
  (no aliases); `/skill` is kept and pipy now advertises discovered skills in the
  system prompt (loaded via the `read` tool); theme selection moved into
  `/settings`. `--verbose` and `--offline` now ship: verbose overrides `quietStartup` for startup chrome without changing settings, and offline sets pipy's startup network guards. Tool allow/deny flags (`--tools`/`-t`, `--exclude-tools`/`-xt`, `--no-tools`/`-nt`, and `--no-builtin-tools`/`-nbt`) now ship through the native tool-loop boundary.

The extension/package closeout changed the next-topic ordering, the
export/import/share/distribution baseline has since landed, and the
product-TUI long-input wrapping gap is now closed. Core local extension
workflows, local-path and managed-git package runtime composition, package
update, product export/import/share, and soft-wrapped long editable prompts now
ship. Extension/package work remains the largest follow-on area, but its next
slices are richer platform APIs and any future PyPI/npm source policy rather
than the just-landed package cache/update runtime.

## Ranked biggest gaps

### 1. Product-TUI long-input wrapping — shipped

Pipy now soft-wraps long typed input inside the input frame, with the cursor
moving across wrapped rows while footer/status rows stay pinned. The renderer
uses wrapped input frame rows with cursor metadata instead of the former
horizontally scrolling one-row projection, and the provider still receives the
literal submitted text including pasted newlines.

Shipped in pipy:

1. Replaced `ToolLoopTerminalUi._input_view(width)` and the one-row input
   `_FrameLine` projection with a soft-wrapped input region.
2. Reserved dynamic input height in the live-region budget while keeping footer
   and status rows pinned.
3. Mapped cursor index to wrapped row/column and preserved literal submitted text,
   including pasted newlines.
4. Added real-PTY coverage at 80x24 and 100x40 for long typing, paste, cursor
   movement, and resize.
5. Updated docs/spec rows that previously described horizontal scrolling as
   shipped parity.

### 2. Export / import / share / distribution — baseline shipped

**Why it is first now:** the native session tree now stores full product
sessions and the extension/package slice-12 closeout has landed, so Pi-style
full export/import/share is unblocked and comparatively bounded.

Pi reference:

- `core/export-html/*`: self-contained HTML export with embedded session data.
- `agent-session.ts`: `exportToHtml`, `exportToJsonl`.
- `agent-session-runtime.ts`: `importFromJsonl`.
- `interactive-mode.ts`: `/export`, `/import`, `/share`, `/changelog`.
- `main.ts` and `args.ts`: `--export <file>` export-and-exit.
- `package-manager-cli.ts`, `version-check.ts`, and config helpers: install
  docs, version checks, update command family.

Pipy current state:

- Product native sessions exist and contain full transcripts.
- `/export`, `/import`, `/share`, top-level `--export`, and self-update
  planning now ship through pipy-owned stdlib boundaries.
- `pipy-session export` remains metadata-only and is not the product parity
  export path.

The export conformance gate has been added and passes:

```sh
uv run python scripts/parity_checks/export_distribution_conformance.py --json
just check
```

### 3. Extension and package platform follow-ons

**Why it remains important:** Pi's extension/package story is still broader and
more mature. Pipy is now Pi-shaped for core local extension workflows, but not
Pi-equivalent as a platform.

Pipy current state:

- `docs/extension-api.md` defines and tracks a Python-only, Pi-shaped API.
- Extension slices 1–12 have shipped: local discovery/inventory, activation,
  command dispatch, `tool_call` gates, lifecycle/input/before-agent-start hooks,
  extension tool registration, `tool_result` transforms, minimal UI
  notification, golden conformance, shortcuts, provider-registration mechanics, OAuth metadata preservation,
  catalog/`/model` wiring for extension-registered providers, local-path and
  managed git package CLI, package runtime composition for installed package
  resources, package `update`, and per-run source-loading flags for explicit
  extensions, skills, prompt templates, and themes.
- Extension slice 13 has shipped: live-session hooks for `user_bash`,
  `before_provider_request`, session switch/fork/tree/compaction gates, and
  dynamic active tool/model/thinking controls through the command/hook context.
- Extension slices 14–16 have shipped: dynamic `pipy repl` tool-loop CLI flags
  (`ExtensionFlag` + `ctx.flags`), simple command/shortcut UI primitives
  (`ctx.ui.select`/`input`/`confirm`/`set_status`/`set_working_*`), and the
  first custom session-entry/message-rendering slice
  (`api.register_message_renderer` + `ctx.append_entry`).
- Extension slice 17 has shipped: custom tool renderers — `ExtensionTool`
  `render_call`/`render_result` callables render an extension's own tool
  call/result rows with themed color (render-once snapshot, fail-soft fallback)
  in both the product TUI and captured output. The renderer map now refreshes
  across `/reload`, so added/changed/removed extension renderers take effect in
  the existing session.
- Extension slice 18 has shipped: persistent chrome widgets —
  `ctx.ui.set_widget`/`set_header`/`set_footer`/`set_title`/`set_working_indicator`
  pin an above/below-editor widget, an exclusive custom header and footer, the
  terminal title, and a custom working indicator, with a width-reactive snapshot
  model, fail-soft rendering, dispose-on-replace/clear, and live `session_start`
  rendering in an interactive TTY.
- Extension slice 19 has shipped: rich message renderers (rich-UI item C) — a
  `register_message_renderer` renderer that requires a second `(data, ctx)`
  parameter receives a `MessageRenderContext` and may return a themed component,
  committed SGR-preserving with no forced `[custom_type]` label (render-once
  snapshot at append width, fail-soft to the plain path); a 1-arg
  `renderer(data)` keeps slice-16 plain behavior. The rendered body is live-only
  and never archived. Active-branch custom entries now replay into
  startup-opened TUI sessions through the same renderer dispatch without
  mutating the session file.
- Extension slice 20 has shipped: the command/shortcut `ctx.ui.editor(...)`
  helper opens a focused multi-line product-TUI overlay, returns `None`
  headlessly like Pi's no-op UI context, submits on Enter, accepts Shift+Enter
  where decoded plus Alt+Enter as pipy's portable newline fallback, and cancels
  on Esc/Ctrl-C. The editor also matches Pi's Ctrl+G `$VISUAL`/`$EDITOR`
  handoff: it restores normal terminal mode while the external editor owns
  stdio, reloads the temp markdown file only on a successful exit, and keeps the
  prior buffer on failure.
- Extension slice 21 has shipped: command/shortcut theme controls (rich-UI
  item E) — `ctx.ui.theme` (current `ChromePalette`), `ctx.ui.get_all_themes()`
  (`{"name", "path": None}` per available theme, default first),
  `ctx.ui.get_theme(name)` (palette by name without switching, `None` when
  unknown), and `ctx.ui.set_theme(name_or_palette)` (`{"success", "error"}`),
  mirroring Pi's `theme`/`getAllThemes`/`getTheme`/`setTheme`. Reads are ambient
  (the global package theme registry plus `PIPY_THEME`/the chrome store), so they
  work deterministically even headless; `set_theme` requires a live UI and
  returns `{"success": False, "error": "UI not available"}` headless without
  mutating process state, while a live call reuses the `/settings` `select_theme`
  mechanism so the next frame repaints. `path` is always `None` (the session
  theme registry retains only `name -> palette`; package file paths are not
  exposed to extension code).
- Extension slice 22 plus session metadata actions have shipped: `ctx.session_manager`
  plus Pi-shaped `ctx.sessionManager` expose active-session read-only views, and
  command/shortcut contexts can persist display names and labels with
  `ctx.set_session_name` / `ctx.setSessionName`, `ctx.get_session_name` /
  `ctx.getSessionName`, and `ctx.set_label` / `ctx.setLabel`, all through native
  session-info/label entries rather than mutable tree exposure.
- Extension slice 23 has shipped: live product-TUI autocomplete provider
  wrappers — `ctx.ui.add_autocomplete_provider` plus Pi-shaped
  `ctx.ui.addAutocompleteProvider` compose providers with Pi-shaped
  `get_suggestions`/`apply_completion`/optional
  `should_trigger_file_completion` methods for `@` and forced Tab completion,
  while headless contexts remain deterministic no-ops.
- Extension slice 24 has shipped: Pi-shaped custom editor component store —
  `ctx.ui.set_editor_component` / `setEditorComponent` and
  `ctx.ui.get_editor_component` / `getEditorComponent` retain an opaque factory
  object in live command/shortcut contexts and clear it on `None`, while
  headless contexts no-op/return `None` like Pi RPC. Full custom editor
  rendering/input integration remains deferred.
- Extension slice 25 has shipped: tool-output expansion controls —
  `ctx.ui.get_tools_expanded` / `getToolsExpanded` and
  `ctx.ui.set_tools_expanded` / `setToolsExpanded` read and set the live
  product-TUI expansion state used by built-in tool-row expansion, repainting on
  writes; headless contexts return `False` and no-op writes like Pi RPC.
- Package resources now flow through discovery at deterministic lowest
  precedence with filters applied, and the package conformance gate proves no
  source path or resource body leaks to safe metadata. The same gate now covers
  explicit source-loading paths with matching default discovery and persisted
  filters disabled.

Follow-ons:

1. Richer Pi extension APIs: full custom editor component rendering/input
   integration beyond the landed `setEditorComponent` in-memory store,
   live per-frame
   component `render()`/`requestRender` re-rendering of chrome
   components (the working indicator already animates via the spinner loop) /
   reactive `footerData` beyond the landed width-reactive chrome snapshot,
   *multi-widget* message components beyond the landed single-component rich
   message renderer (item C), richer tool-output expansion integration beyond
   the landed live `getToolsExpanded`/`setToolsExpanded` controls, and the deferred message-entry follow-ons beyond shipped idle
   `send_message` `triggerTurn` / `deliverAs: "nextTurn"` delivery (streaming
   `steer`/`followUp`, in-session full-history redraw on `/resume` switches,
   rendering a `CustomMessageEntry` beyond stored display replay), live tool-render invalidation beyond the landed
   render-once snapshot, threading the live `ui_driver` into non-lifecycle event hooks
   (`tool_call`/`tool_result`/`input`/`user_bash`/`before_*`) so their chrome
   calls paint immediately, broader dynamic-flag integration beyond the landed
   tool-loop `ctx.flags` and extension-owned `api.get_flag` slice, and broader extension state helpers beyond the landed command/shortcut
   session-manager view and name/label metadata actions.
2. OAuth-provider extension `/login` and auth-storage wiring plus broader provider/auth helpers (metadata registration now ships).
3. Future PyPI/npm package sources only after a broader supply-chain/update
   policy; managed git sources and package `update` now ship.

### 4. User documentation parity

**Why it is needed:** pipy now has enough shipped product surface that internal
specs are no longer sufficient. Pi has user-facing pages for installation,
first run, providers, settings, keybindings, sessions, compaction,
customization, automation, SDK/RPC, and terminal/platform setup.

Implement in pipy:

1. User-facing quickstart and usage pages now ship:
   [quickstart.md](quickstart.md) and [usage.md](usage.md) cover first run,
   provider setup, common TUI/session workflows, and the current CLI reference.
   Terminal setup and tmux pages also ship:
   [terminal-setup.md](/terminal-setup/), [tmux.md](/tmux/).
2. Provider/model user docs now ship in [providers.md](providers.md): model
   listing, provider selection, credentials, `models.json`, ds4, thinking/images
   metadata, and current follow-ons.
3. Settings and keybindings user docs now ship in [settings.md](settings.md)
   and [keybindings.md](keybindings.md).
4. Remaining user docs: customization, automation, SDK/RPC, and install/update
   deep dives.
5. Keep README short and outside-in.
6. Separate shipped behavior from target specs; do not present pipy-only
   divergences as parity.
7. Audit docs against `uv run pipy --help`, `uv run pipy repl --help`,
   `uv run pipy run --help`, and the relevant conformance gates.

Owning spec: [user-documentation.md](user-documentation.md).

### 5. Provider/model catalog follow-ons

**Why it is now narrower:** the catalog construction foundation has shipped for
the implemented adapter families, one-shot runs, and startup resolution.
Remaining work is adapter/product polish rather than the broad catalog track.

Follow-ons:

- live Anthropic and GitHub Copilot login UX;
- Vertex API-key auth;
- Anthropic adaptive-thinking request shape;
- Azure URL/api-version parity;
- broader local-provider maturity and benchmarking; and
- broader local-provider maturity and benchmarking.

Owning spec: [provider-catalog.md](provider-catalog.md).

### 6. Top-level CLI compatibility and parity cleanup — largely shipped (2026-06-20)

The 2026-06-20 top-level CLI cleanup landed the bulk of this item across four
code slices:

- **Top-level shape is now Pi-like:** bare `pipy` and `pipy "<prompt>"` launch
  the interactive product session (a bare positional prompt seeds the first
  message); `auth|run|repl|config|install|...` remain reachable as subcommands.
  Reserved-word exception: a bare token equal to a subcommand name dispatches
  that subcommand (escape via `pipy repl "<word>"` / `pipy -p "<word>"`).
- **Removed (hard):** the no-tool REPL (`--repl-mode`,
  `NativeNoToolReplSession`, and `/read` `/ask-file` `/propose-file`
  `/apply-proposal` + their archive-side events), `--native-output json`
  (callers use `--mode json`), the `--archive-transcript` sidecar (the native
  session tree is the transcript), the pipy-only `/template` wrapper, and the
  pipy-only `/clear`, `/status`, `/help`, and `/theme` commands (removed outright,
  no deprecation aliases or notices; Pi has none — use `/new`, `/session`,
  `/hotkeys`, and theme selection in `/settings`).
- **Templates:** prompt templates are invokable as their own `/<template-name>`
  commands.

**The two earlier follow-ups are now done:**

- `/skill` is **kept** — Pi advertises skills in the system prompt *and* keeps a
  `/skill:name` expansion, so pipy's `/skill` is parity-consistent. pipy now also
  wires its own advertisement: discovered skills are advertised in the tool-loop
  system prompt (name + description + absolute location) when the `read` tool is
  available, and the model loads a skill body on demand via `read` (skill dirs
  are added to the read-only reference roots).
- Theme selection moved into the `/settings` dialog (a theme row + picker); the
  pipy-only `/theme` command was removed outright.

`--read-root(s)`, `--tool-budget`, `--input-runtime`, and the persistent prompt
history are kept as internal mechanisms (decision 3), de-emphasized in docs as
internal, not parity features.

### 7. Verification policy through extensions

The former pipy-only `/verify just-check` command is gone. Richer verification
or permission policy should arrive as extension-defined tools/hooks once the
extension platform exists, matching Pi's extensibility posture rather than
adding another bespoke slash command.

## Recommended implementation order

1. Extension/package platform follow-ons: richer multi-widget UI/rendering,
   broader extension state/session-manager helpers, live tool-render invalidation beyond
   the landed render-once snapshot, broader dynamic-flag integration,
   OAuth-provider extension `/login` wiring, and future PyPI/npm package source
   policy.
2. User documentation parity in parallel with implementation.
3. Focused provider/model catalog follow-ons.
4. Top-level CLI compatibility and pipy-only surface cleanup — shipped
   (2026-06-20), including the system-prompt skill advertisement (`/skill` kept),
   theme selection in `/settings`, and the outright removal of `/clear`,
   `/status`, `/help`, and `/theme` (no deprecation shims).
5. Verification/project policy through extension gates, not a revived `/verify`
   command.

The extension/package platform remains the largest follow-on by surface area,
but its local runtime plus managed git package/update slices have landed.
Future extension/package work should start from that shipped package-runtime
baseline and keep PyPI/npm source execution behind a broader supply-chain
policy.
