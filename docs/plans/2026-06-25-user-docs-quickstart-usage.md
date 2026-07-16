# User docs quickstart + usage parity plan

## Gap

User documentation parity, slice 2 from `docs/user-documentation.md`: add pipy user-facing `quickstart.md` and `usage.md`, wire them into docs navigation, and shorten the outside-in README path so users do not need maintainer specs for first-run and daily usage.

## Pi reference

Reference pages:

- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/quickstart.md`
- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/usage.md`

Pi's pages cover install, auth, first session, project instructions, common editor/session/one-shot workflows, slash commands, session flags, package commands, mode/model/session/tool/resource options, and next-step links.

## Pipy approach

Add pipy-owned user docs that follow the Pi page shape but state pipy's shipped behavior:

1. `docs/quickstart.md`
   - local checkout/development install and future published install placeholders;
   - first `pipy` run and deterministic fake-provider smoke path;
   - real-provider setup via environment variables/auth where shipped;
   - project instruction files and `/reload`;
   - common workflows: `@file`, shell shortcuts, model switching, sessions, one-shot/headless modes;
   - local state paths, distinguishing the native product session tree from the separate `pipy-session` catalog.
2. `docs/usage.md`
   - interactive product TUI areas and editor features that currently ship;
   - slash command table matching the shipped Pi-shaped command set;
   - queued message behavior, context files, export/share, CLI reference, package/mode/model/session/tool/resource/system-prompt options;
   - explicit notes for pipy-only or not-yet-complete areas instead of presenting them as Pi parity.
3. Wire both pages into `docs/index.md` and `zensical.toml`, and shorten README's user path by linking to them from the top-level orientation.
4. Update parity docs/backlog/audit to mark the quickstart + usage documentation slice shipped while leaving provider/session/settings/etc. docs as remaining user-documentation work.

## Constraints

- Documentation-only slice; do not change runtime behavior.
- Audit command/flag names against current `uv run pipy --help`, `uv run pipy repl --help`, and `uv run pipy run --help` output.
- Keep README concise and outside-in.
- Do not copy Pi text verbatim where it would imply unsupported npm/Pi behavior; use pipy names, state paths, and current limitations.
- Update `zensical.toml` for new pages.

## Done when

- `docs/quickstart.md` and `docs/usage.md` exist and are linked from README, `docs/index.md`, and docs nav.
- The docs describe shipped pipy behavior and clearly label current limitations.
- `docs/user-documentation.md`, `docs/backlog.md`, and `docs/pi-mono-gap-audit.md` reflect that the quickstart + usage slice has shipped.
- `just docs-build` and `just check` pass.
- A different-family review returns CLEAN over the plan, and later over the complete implementation diff.
