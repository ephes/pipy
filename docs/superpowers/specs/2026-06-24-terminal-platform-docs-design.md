# Terminal Platform User Docs Design

## Gap

`docs/user-documentation.md` identifies terminal/platform docs as one missing
Pi-style user documentation slice. Pi's reference pages are:

- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/terminal-setup.md`
- `/Users/jochen/src/pi-mono/packages/coding-agent/docs/tmux.md`

Pipy has shipped the relevant terminal behavior, but users currently have to
read README sections, `docs/tui-workflow.md`, or maintainer planning docs to
understand terminal expectations.

## Scope

Add two user-facing docs:

- `docs/terminal-setup.md`: supported terminal expectations, multiline input
  keys, paste/image/drop behavior, clipboard behavior, terminal-title/theme
  behavior, platform caveats, and when to prefer `--mode json`/`--print`.
- `docs/tmux.md`: tmux configuration for modified keys, what the config fixes,
  scrollback behavior, live verification commands, and common troubleshooting.

This slice is documentation only. It must describe shipped pipy behavior first
and call out Pi differences where pipy intentionally uses a portable fallback,
for example Alt+Enter as the reliable newline chord when Shift+Enter is not
decoded by the terminal.

## Non-Goals

- Do not copy Pi's TypeScript or `pi-tui` component docs.
- Do not claim Kitty keyboard protocol support unless pipy's shipped behavior
  depends on it.
- Do not add runtime terminal features.
- Do not mark the whole user-documentation track complete.

## Documentation Updates

- Link both pages from `docs/index.md`.
- Update `docs/user-documentation.md` to mark implementation slice 8 as shipped.
- Update `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, and
  `docs/parity-plan.md` to show terminal/platform docs as landed while the
  broader user-documentation parity track remains open.

## Done When

- `docs/terminal-setup.md` and `docs/tmux.md` exist and are written from a user
  point of view.
- Navigation and parity tracking docs link to the new pages.
- `just docs-build` and `just check` pass.
- Opus review returns CLEAN over the final docs diff.
