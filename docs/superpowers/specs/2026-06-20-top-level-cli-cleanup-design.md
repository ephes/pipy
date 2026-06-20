# Top-level CLI compatibility & accidental-surface cleanup — Design

Status: design approved 2026-06-20. Owning parity plan section:
[parity-plan.md](../../parity-plan.md) §2 (CLI flag/mode matrix) and §3
(accidental pipy-specific surfaces). This spec turns the §3 "remove or realign"
rows and the §2 top-level-shape gap into a concrete, sliced implementation.

## Goal

Make pipy's command surface behave like Pi's:

- `pipy` (bare) and `pipy "<prompt>"` launch the interactive product session, the
  same way `pi` and `pi "<prompt>"` do — no required subcommand.
- The pipy-only surfaces that exist only because of pipy's history (the no-tool
  REPL, the metadata-only automation output, the transcript sidecar, and the
  pipy-only slash commands) are removed or realigned to their Pi equivalents.
- pipy keeps a small set of non-divergent internal conveniences, but stops
  presenting them as parity features.

This closes one of the two remaining "real parity done" criteria in
parity-plan.md §6 (the §3 accidental surfaces); user-documentation parity is the
other and is a separate topic.

## Guiding principle

From parity-plan.md: a surface that exists only in pipy and not in Pi is removed
or realigned unless there is a genuinely good reason to keep it. Privacy and
security are explicitly not good reasons. This spec applies that rule to the CLI
and slash-command surface.

## Decisions (approved 2026-06-20)

1. **Top-level dispatch: full Pi-shape, keep aliases.** Bare `pipy` and a bare
   positional prompt route to the interactive product path; the existing
   `auth|run|repl|config|install|remove|uninstall|list|update` subcommands keep
   working so nothing breaks.
2. **Removal policy: pragmatic mix.** Hard-remove internal/dead surfaces (the
   no-tool REPL and its `/read` `/ask-file` `/propose-file` `/apply-proposal`
   family, `--native-output json`, `--archive-transcript`). Realign user-facing
   slash commands as deprecated aliases (`/clear`→`/new`, `/status`→`/session`,
   `/theme`→`/settings` theme selection) for one cycle.
3. **Kept extras: internal mechanisms.** `--read-root(s)`, `--tool-budget`,
   `--input-runtime`, and the persistent prompt history stay in the code as
   non-divergent conveniences but are de-emphasized in docs (not presented as
   parity features). No behavior change.

## Architecture: top-level dispatch (Approach A — front-controller router)

`src/pipy_harness/cli.py` (`build_parser()` ~107–645, `main(argv)` ~752–1075)
keeps its argparse subparser layout. A thin router runs **before** argparse
dispatch in `main()`:

```
known_subcommands = {auth, run, repl, config, install, remove, uninstall, list, update}
top_level_only    = {-h, --help, -v, --version, --export}

route(argv):
  first_token = first element of argv that is not consumed by a top-level-only option
  if argv is empty:                      -> inject "repl"  (bare pipy -> interactive)
  if first_token in known_subcommands:   -> dispatch unchanged
  if argv starts with a top_level_only:  -> dispatch unchanged (help/version/export)
  otherwise:                             -> inject "repl" at the front so the
                                            remaining tokens (positional prompt,
                                            repl flags, @files) are parsed by the
                                            existing repl subparser
```

Rationale: the `repl` subparser already owns every interactive/automation flag
(`--model`, `--print`/`-p`, `--mode`, session flags, `@files`, system-prompt
flags). Injecting `repl` reuses all of it with no parser duplication. Approach B
(promoting the repl args to the top-level parser) yields identical user behavior
but requires invasive surgery on the 2593-line `cli.py`; it is deferred as an
optional later internal refactor.

### Behavior change required for the shim

Today a bare positional prompt is rejected as ambiguous in the interactive REPL
(`cli.py` ~1017–1020: "a positional prompt requires --print/-p"). For Pi-shape
`pipy "<prompt>"`, the interactive product path must accept a bare positional
prompt as the **initial message** of the interactive session. One-shot
(`--print`/`-p`) and `--mode json|rpc` positional handling are unchanged;
`--mode rpc` continues to reject a positional prompt.

### Edge cases the routing matrix must cover

- `pipy` → interactive.
- `pipy "do X"` → interactive with "do X" as the initial message.
- `pipy --model <m>` / `pipy -p "x"` / `pipy @file.py "summarize"` → repl path.
- `pipy repl …`, `pipy run …`, `pipy auth …`, `pipy config …`, etc. → unchanged.
- `pipy --help` / `-h` / `--version` / `-v` / `--export <f>` → top-level handling,
  never re-routed.
- A token that looks like a subcommand name but follows a prompt (e.g.
  `pipy "run the tests"`) is a prompt, not the `run` subcommand, because the
  first token (`"run the tests"`) is a single positional string, not `run`.
- **Reserved-word exception:** a bare first token that is *exactly* a subcommand
  name (`pipy auth`, `pipy run`) always dispatches that subcommand, even if the
  user meant it as a one-word prompt. This is an intentional consequence of
  keeping subcommands as aliases (decision 1) and is the one place pipy's
  top-level shape cannot match Pi's (Pi has no subcommands to collide with). To
  use such a word as a prompt, use an explicit form: `pipy repl "auth"`
  (interactive) or `pipy -p "auth"` (one-shot). Multi-word prompts are
  unaffected, since they are a single quoted positional token. The routing
  matrix test covers this case explicitly.

## Slices

Each slice is TDD'd, gated by a Pi review loop until CLEAN, and committed
separately. `just check` must be green at the end of every slice.

### Slice 1 — Retire the no-tool REPL + proposal/apply family (hard remove)

Done first to remove `--repl-mode` ambiguity before the top-level shim lands.

- Remove the `--repl-mode {auto,no-tool,tool-loop}` flag (`cli.py` ~256–269) and
  collapse mode resolution (`_resolve_repl_mode()` ~1829–1891) so the product
  REPL is always the tool-loop session.
- Delete `NativeNoToolReplSession` (`native/session.py` ~865–1868) and the no-tool
  command handlers `/read`, `/ask-file`, `/propose-file`, `/apply-proposal`
  (~1393–1586, 1871–1929) plus the archive-side observation/patch-proposal tool
  family they emit.
- Remove the no-tool adapter builder (`_repl_adapter_for`, referenced ~988) and
  the no-tool entries from the slash-command completion/description registries
  (`native/repl_input.py` ~29–77).
- Migrate `@file`-context coverage (`test_native_at_file_context_cli.py`, which
  uses `--repl-mode no-tool`) onto the tool-loop path; delete no-tool-only tests.
- `pipy run` (one-shot partial capture via `PipyNativeAdapter`) is independent of
  the no-tool REPL and is **not** touched here.

Tests: removed flag rejected with a clear error; `@file` excerpts still load in
the tool-loop product session; no references to the deleted commands remain.

### Slice 2 — Top-level Pi-shape dispatch (Approach A)

- Add the front-controller router in `main()` with the routing matrix above.
- Accept a bare positional prompt as the interactive initial message.
- Keep all subcommands reachable.
- Update `pipy --help` so the top-level usage reads as a single product command
  with subcommands as secondary, matching Pi's help shape as closely as argparse
  allows.

Tests: a routing matrix table test (bare / positional / repl-flags / each
subcommand still reachable / `--help` / `--version` / `--export` not re-routed);
positional-prompt-as-initial-message behavior.

### Slice 3 — Realign user-facing slash commands (deprecated aliases)

In the tool-loop product session (`native/tool_loop_session.py`) and the
completion/description registries (`native/repl_input.py`):

- `/clear` → deprecated alias of `/new` in the product tool-loop session: it
  prints a one-line deprecation notice pointing to `/new`, then performs the
  `/new` action. Slice 3 adds this alias to the tool-loop session if it is not
  already present there (it currently lives in the now-deleted no-tool REPL), so
  users who type `/clear` get a migration path rather than an unknown-command
  error. The alias is removed in a later cycle.
- `/status` → deprecated alias of `/session` in the product tool-loop session,
  with the same notice and add-if-absent behavior as `/clear`.
- `/theme` → deprecated alias that routes to theme selection under `/settings`
  (theme is already reachable via settings; this ensures a single Pi-shaped
  entry point). The `--theme`/`--no-themes` load flags are unchanged.
- `/skill` and `/template` wrappers dropped: skills are auto-injected into the
  system prompt (Pi's model), and prompt templates register as their own
  `/<template-name>` slash commands rather than going through a `/template`
  dispatcher.
- `/help` kept as an alias of `/hotkeys`.

Tests: each deprecated alias dispatches to its target and emits the deprecation
notice exactly once; prompt-templates are invokable as `/<name>`; skills appear
in the system prompt without a `/skill` call; the completion list reflects the
realigned set.

### Slice 4 — Retire dead automation flags (hard remove)

- Remove `--native-output json` (`cli.py` ~169–178, consumer ~800/893–894,
  `_native_json_output()` ~1260–1301). Callers move to `--mode json`. `pipy run`
  retains its default human/exit-code behavior.
- Remove `--archive-transcript` (`cli.py` ~313–321) and the `TranscriptSink`
  wiring (~1945–1949). The native session tree is the transcript.

Tests: `--native-output json` rejected with an error that points to `--mode
json`; `--archive-transcript` rejected with an error that points to the native
session tree (the default transcript) and `/export`/`--export` — it has no
`--mode json` equivalent; `pipy run` still finalizes its record without the
metadata-only JSON object.

### Slice 5 — Docs / help / changelog sync + de-emphasize kept extras

- parity-plan.md §3: mark the no-tool REPL, proposal/apply commands,
  `--native-output json`, `--archive-transcript`, `/clear`, `/status`, `/theme`,
  `/skill`, `/template` rows as removed/realigned; update §2 top-level-shape and
  §1 slash-command rows.
- pi-mono-gap-audit.md §6 and pi-parity.md: reflect the shipped CLI realignment.
- settings-config.md: theme selection via `/settings`; the `/skill`/`/template`
  realignment.
- CHANGELOG.md `[Unreleased]`: one entry per slice's user-visible change.
- De-emphasize `--read-root(s)`, `--tool-budget`, `--input-runtime`, and the
  persistent prompt history in docs as internal mechanisms, not parity features
  (no code change).

## Out of scope

- Approach B (promoting repl args to the top-level parser) — optional later
  refactor; not required for the user-visible Pi-shape.
- Removing the kept extras (decision 3 keeps them).
- `--verbose` / `--offline` and tool allow/deny flags (`--tools`/`--no-tools`/
  `--no-builtin-tools`/`--exclude-tools`) — separate §2 parity gaps, not part of
  the cleanup.
- User-documentation parity (separate topic).

## Testing & verification

- Per slice: unit/CLI tests as listed, plus PTY coverage where TUI behavior
  changes (slice 3).
- `just check` (lint + typecheck + test) green at the end of each slice.
- Pi review loop (`pi-review-loop`) run per slice; only commit a slice after Pi
  returns CLEAN.

## Risks

- **Slice 1 is a large deletion** touching shared paths in `session.py` and
  `tool_loop_session.py`. Mitigation: do it first, lean on the existing suite,
  and keep `pipy run` (a separate adapter) out of scope.
- **`/skill` auto-injection is a behavior change** beyond aliasing. It is
  included in slice 3 per approval; if it proves large it can split into its own
  slice without changing the rest.
- **Deprecated-alias churn**: the aliases are transitional and scheduled for
  removal in a later cycle; the deprecation notice makes that explicit.
