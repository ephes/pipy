# Changelog

All notable changes to pipy are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/); `/changelog` renders these
entries oldest-first, and a version bump shows the new entries at startup.

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
