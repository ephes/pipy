# CLI `--verbose` / `--offline` parity plan

Gap: Pi accepts top-level `--verbose` and `--offline` in `packages/coding-agent/src/cli/args.ts`. `--verbose` forces startup chrome even when `quietStartup` is true; `--offline` sets offline env guards early (`PI_OFFLINE=1`, `PI_SKIP_VERSION_CHECK=1`) so startup network operations are disabled. Pipy's parity docs still list both as missing.

Scope for this single slice:

1. Add `--verbose` and `--offline` to pipy's REPL/product CLI surface and top-level router so `pipy --verbose` / `pipy --offline` route to the implicit `repl` command like Pi-style product flags.
2. Thread `verbose` through the native tool-loop session startup and reload chrome decisions so it overrides `quietStartup` without changing persisted settings.
3. Apply `offline` early in `pipy_harness.cli.main` by setting pipy's existing offline environment guards (`PIPY_OFFLINE=1`, `PIPY_SKIP_VERSION_CHECK=1`) before any update/version-check-capable startup work.
4. Add focused tests for parser/routing/env behavior and the startup chrome quiet/verbose decision.
5. Update parity docs (`docs/parity-plan.md`, `docs/pi-mono-gap-audit.md`, `docs/backlog.md`) to mark the flags shipped and keep the remaining gap list accurate.

Done when: focused tests pass, `just check` passes, and a different-family review returns CLEAN over the complete diff.
