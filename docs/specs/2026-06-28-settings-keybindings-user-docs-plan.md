# Settings and keybindings user-docs parity plan

Gap: user documentation parity still lists settings/keybindings docs as remaining, while Pi ships user-facing `docs/settings.md` and `docs/keybindings.md` in `/Users/jochen/src/pi-mono/packages/coding-agent/docs/`.

Scope: add two user-facing pipy pages, `docs/settings.md` and `docs/keybindings.md`, and wire them into site navigation/index/backlog/parity tracking. This is a documentation-only parity slice; no runtime behavior changes are intended.

Pi reference summary:

- Settings locations: Pi uses `~/.pi/agent/settings.json` globally and `.pi/settings.json` per project; pipy equivalent is the existing shipped settings manager using `PIPY_CONFIG_HOME`/`${XDG_CONFIG_HOME}/pipy`/`~/.config/pipy` for global `settings.json`, plus `.pipy/settings.json` for project overrides.
- Pi settings fields to document when relevant to pipy: `defaultProvider`, `defaultModel`, `defaultThinkingLevel`, `hideThinkingBlock`, `thinkingBudgets`, `theme`, `quietStartup`, `collapseChangelog`, `enableInstallTelemetry`, `doubleEscapeAction`, `treeFilterMode`, `editorPaddingX`, `autocompleteMaxVisible`, `showHardwareCursor`, `warnings.anthropicExtraUsage`, `compaction.*`, `branchSummary.*`, `retry.*`, delivery/transport keys, terminal/image keys, shell/package keys, `sessionDir`, `enabledModels`, Markdown/resource arrays (`packages`, `extensions`, `skills`, `prompts`, `themes`, `enableSkillCommands`). Pipy docs must label unsupported/no-op or pipy-divergent items instead of implying full behavior.
- Keybindings locations/shape: Pi uses `~/.pi/agent/keybindings.json`; pipy equivalent is `<config>/keybindings.json`. The JSON maps action ids to a string key or list of alternatives, supports namespaced ids, migrates old flat ids, and reloads with `/reload`.
- Keybinding ids/defaults: document the shipped pipy ids from `pipy_harness.native.keybindings`, which intentionally mirror Pi's documented namespaced groups: `tui.editor.*`, `tui.input.*`, `tui.select.*`, `app.*`, session/model/tree/scoped-model actions.

Implementation tasks:

1. Add `docs/settings.md` with outside-in guidance: file locations, precedence/deep merge, editing/reload, common examples, field reference grouped by behavior, privacy notes, and Pi/pipy divergences.
2. Add `docs/keybindings.md` with outside-in guidance: location, key format, action id table, examples, migration/reload notes, and current limitations.
3. Wire both pages into `zensical.toml` and `docs/index.md`.
4. Update `docs/user-documentation.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` so this slice is marked shipped and the remaining user-docs gap is narrowed.
5. Add a changelog note under `[Unreleased]` / Added.

Done when:

- The new pages describe shipped pipy behavior without claiming TypeScript/source compatibility or unsupported Pi-only behavior.
- Navigation includes both pages.
- Parity docs no longer list settings/keybindings as missing user docs.
- `just check` and docs build (via `just check`) pass.
