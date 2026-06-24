# Terminal Platform User Docs Implementation Plan

## Goal

Close one user-documentation parity slice by adding pipy-owned user docs for
terminal setup and tmux usage, based on Pi's reference pages but describing
pipy's shipped stdlib TUI behavior.

## Tasks

1. Add `docs/terminal-setup.md`.
   Acceptance: covers supported terminal expectations, multiline input,
   bracketed paste, image/file drops, clipboard image paste, `/copy`, terminal
   title/theme behavior, and fallback automation modes.
2. Add `docs/tmux.md`.
   Acceptance: gives the recommended tmux extended-key configuration, explains
   what it fixes, documents scrollback behavior, and lists troubleshooting
   commands.
3. Wire navigation and tracking docs.
   Acceptance: `docs/index.md`, `docs/user-documentation.md`,
   `docs/backlog.md`, `docs/pi-mono-gap-audit.md`, and `docs/parity-plan.md`
   distinguish this landed slice from the still-open broader documentation
   parity track.
4. Add release-note coverage.
   Acceptance: `CHANGELOG.md` mentions the new terminal/tmux user docs.
5. Verify.
   Acceptance: `just docs-build`, `just check`, and Opus review return clean.
