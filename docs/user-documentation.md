# Pipy User Documentation Parity

Status: target specification added after the provider-catalog documentation review.

Pipy's current documentation is strong for implementation planning, parity
tracking, and coding-agent handoff, but it is not yet Pi-equivalent **product
user documentation**. Pi has user-facing pages for installation, first run,
usage, providers, settings, keybindings, sessions, compaction, customization,
automation, SDK/RPC, and terminal/platform setup. Pipy should grow the same
kind of outside-in documentation while keeping the existing specs for agents and
maintainers.

This is documentation parity, not runtime feature parity. A page can start as a
stub when the corresponding feature is not implemented, but it must state the
current pipy behavior and link to the target spec rather than silently copying
Pi docs.

## Target Structure

Create or reshape user-facing pages under `docs/`:

- `quickstart.md` — install/setup, first `pipy` run, fake provider smoke, first
  real-provider path, and where local state is written.
- `usage.md` — interactive mode, one-shot mode, slash commands, file/image
  references, tools, and CLI reference from a user point of view.
- `providers.md` — supported providers, env vars/auth, provider catalog,
  `models.json`, `--list-models`, `/model`, `--thinking`, and ds4 as a custom
  provider preset.
- `settings.md` — global/project settings once shipped; until then, current
  local defaults and the settings-config target.
- `keybindings.md` — current TUI keys plus the keybindings target.
- `sessions.md` — native product session tree, `/tree`, `/resume`, `/fork`,
  `/clone`, `/compact`, startup flags, and the separate `pipy-session` catalog.
- `compaction.md` — durable compaction behavior and limitations.
- `skills.md`, `prompt-templates.md`, `themes.md` — current bounded Markdown
  resource behavior and planned Pi realignments.
- `extensions.md`, `packages.md` — clearly mark the Python extension/package
  platform as planned until it ships; do not imply TypeScript compatibility.
- `models.md`, `custom-provider.md` — `models.json` schema, examples, routing,
  auth, and local/custom-provider setup.
- `json.md`, `rpc.md`, `sdk.md` — current SDK/streaming surfaces and the
  planned Pi-style JSON/RPC modes.
- `terminal-setup.md`, `tmux.md`, and optional platform notes — terminal
  behavior, scrollback, bracketed paste, keyboard caveats, and PTY testing
  expectations.

Keep `architecture.md`, `harness-spec.md`, `parity-plan.md`, `backlog.md`, and
the per-topic specs as maintainer/agent documentation. User pages should link to
them only for deeper design detail.

## Documentation Principles

- User docs describe shipped behavior first, then clearly label planned parity
  work.
- Do not present pipy-only divergence as Pi parity. If a command/flag exists only
  in pipy, say so and link to the cleanup/realignment plan.
- Keep command lists generated or manually audited against `uv run pipy --help`,
  `uv run pipy repl --help`, and the relevant slash-command dispatcher.
- Keep provider/model docs audited against
  `scripts/parity_checks/provider_catalog_conformance.py --json` and
  `uv run pipy repl --list-models`.
- Keep session docs audited against
  `scripts/parity_checks/session_tree_conformance.py --json`.
- Document privacy/storage split plainly: the native session tree is the product
  transcript; `pipy-session` is a separate metadata/catalog utility.
- User docs must not include secrets, tokens, raw transcripts, or local private
  paths except as placeholders.

## Implementation Slices

1. **Docs map and navigation.** Add this spec, link it from `docs/index.md`, and
   keep the current planning docs discoverable.
2. **Quickstart + usage.** Split the user-facing parts of `README.md` into
   `quickstart.md` and `usage.md`; keep README short and link to them.
3. **Provider/model docs.** Turn the provider-catalog foundation and its
   product-wiring status into user docs: provider setup, `models.json`, ds4
   example, `--list-models`, `/model`, the current limits around `--thinking`
   and `--api-key`, auth behavior, and remaining scoped-cycling follow-up.
4. **Session docs.** User-facing native session tree guide and `pipy-session`
   catalog guide, with the store split explicit.
5. **Settings/keybindings docs.** Land alongside the settings-config track as
   the runtime behavior ships.
6. **Customization docs.** Skills/templates/themes now; extensions/packages as
   planned until the extension platform ships.
7. **Automation docs.** JSON/RPC pages land with the automation track; SDK docs
   can start with the current Python SDK.
8. **Terminal/platform docs.** TUI behavior, tmux, paste, scrollback, keyboard
   caveats, and platform-specific notes.

## Review Checklist

Before marking this documentation track complete:

```sh
uv run pipy --help
uv run pipy repl --help
uv run pipy run --help
uv run pipy repl --list-models
uv run python scripts/parity_checks/session_tree_conformance.py --json
uv run python scripts/parity_checks/provider_catalog_conformance.py --json
just docs-build
```

Then manually verify that user docs and README do not contradict those outputs
or the shipped conformance gates.
