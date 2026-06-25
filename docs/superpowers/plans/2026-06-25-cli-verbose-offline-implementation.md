# CLI `--verbose` / `--offline` implementation plan

1. CLI parser and routing
   - Add `--verbose` and `--offline` to the REPL parser.
   - Keep them off `_TOP_LEVEL_ONLY_FLAGS` so bare `pipy --verbose` and `pipy --offline` route to `pipy repl ...`.
   - Acceptance: routing tests cover both bare flags and parser defaults.

2. Offline environment guard
   - In `main`, after routing/parsing and before command dispatch that may build runtime state, set `PIPY_OFFLINE=1` and `PIPY_SKIP_VERSION_CHECK=1` when `args.offline` is true.
   - Acceptance: a focused test proves both env values are set and a no-provider-turn path exits successfully.

3. Verbose startup override
   - Add a `verbose_startup` option to the tool-loop adapter/session boundary.
   - Use it wherever startup/reload chrome currently checks only `settings.get_quiet_startup()` so `verbose` forces chrome without mutating settings.
   - Acceptance: a focused unit test proves quiet settings suppress chrome by default and verbose emits it.

4. Docs and parity status
   - Update parity docs to remove the `--verbose` / `--offline` missing marker and describe the shipped behavior.
   - Acceptance: docs identify any remaining top-level CLI gaps accurately.

5. Gates
   - Run focused tests, `just check`, and a different-family review over the complete code+docs diff before commit.
