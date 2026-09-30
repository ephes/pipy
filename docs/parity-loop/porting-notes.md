# Parity-Loop Porting Notes

Reference notes for the parity loop's Phase 2 plan and Phase 5 implementation
(`skill-body.md`): measured behavior of external tools and Node builtins that
Pi relies on and pipy emulates, plus porting patterns that cost review rounds.
Pin each rule that applies in the plan before review.

## Emulating rg and fd

Measure the real binary before writing the plan: build a scratch tree and drive
the binary from a small subprocess script. The TOOLS1 plan review took four
rounds, and each round found one rule the plan had not pinned. Rules measured so
far:

- `fd` matches basenames at any depth, uses smart case, prints a trailing slash
  on directories, and treats `--max-results 0` as unlimited.
- `rg --glob` is case-sensitive. A glob that contains a slash is relative to
  rg's cwd, not to the search path. A leading `!` negates the glob and prunes
  the matching directories. An explicit file path ignores the glob.
- Ignore files (READ2): a glob override beats an ignore rule;
  `--no-require-git` keeps ancestor rules across nested repos; an unclosed `[`
  is literal; an unclosed `{` skips the line; a leading BOM is stripped.

When a Python walk emulates rg/fd ignore handling, write the differential test
first. Build one scratch tree with every rule class (nested, anchored and
negated `.gitignore`, `.ignore`, `.rgignore`/`.fdignore`, `info/exclude`,
ignore files above the repo root, a nested repo, a linked worktree, brace and
bracket globs, a BOM) and compare pipy against the real binaries run with Pi's
flags. READ2 plan and code review took 4 + 4 rounds, each naming one more rule
above; the differential tree then caught none of them late.

## Node builtins and Pi's own code as the oracle

Port the whole Node contract of each builtin a Pi helper calls, not the happy
path. Two READ2 code-review rounds were churn from partial fixes:

- `fileURLToPath` accepts only an empty or `localhost` host (any case) with no
  credentials or port, and throws otherwise.
- `path.join` keeps the base for a suffix that starts with `/`;
  `posixpath.join` drops it.
- Every tool turns a resolver `ValueError` into an error result, and a
  prompt-reference loop guards each reference, so one bad `@` token cannot block
  the rest.

Node 26 runs Pi's TypeScript directly: a scratch `.mjs` can
`await import('<pi-mono>/packages/coding-agent/src/core/tools/edit-diff.ts')`
(bare imports such as `diff` resolve from pi-mono's `node_modules`), so a random
differential test against Pi's own code needs no tsx. In TOOLS2 it caught JS
`split('')` counting UTF-16 units, not `str.count`, before review.

For text Pi writes with Node, normalize the complete string as a UTF-16 round
trip (surrogate pairs join, lone surrogates become U+FFFD) and encode it before
opening the file. Per-fragment fixes and open-then-encode cost two review rounds
(data loss, then split pairs).

## Completions `thinkingFormat` and compat flags

Rules the openai-completions reasoning slices paid for in review rounds. Pin
each one that applies per variant in the plan.

- **Resolve each compat flag independently.** Pi `getCompat` resolves every
  field on its own (explicit `compat.<flag>` wins, else that flag's
  `detectCompat` predicate). Port each secondary flag as its own bounded
  predicate (explicit bool, else the exclusion list); never default it to true
  because the format flag "implies" it. Example: explicit
  `compat.thinkingFormat="deepseek"` on a base URL excluded from
  `supportsReasoningEffort` (isGrok/isZai/isMoonshot/isTogether/
  isCloudflareAiGateway/isNvidia/isAntLing) emits `thinking:{type:enabled}` with
  no `reasoning_effort`; test that mismatch.
- **Not every variant has a secondary flag.** The `enable_thinking` family
  (`zai`, `qwen`, `qwen-chat-template`) emits only
  `enable_thinking = !!options.reasoningEffort` (openai-completions.ts:556-563)
  and never reads `supportsReasoningEffort`. The omission is structural, not an
  exclusion: test the inverse (explicit `supportsReasoningEffort=true` still
  omits `reasoning_effort`). `qwen-chat-template` also forces a literal
  `preserve_thinking: true` in both reasoning states.
- **Port detection rungs at their Pi position.** The `thinkingFormat` chain is
  isDeepSeek > isZai > isTogether > isAntLing > isOpenRouter. Appending a rung
  after one that comes later in Pi changes collision rows; add a precedence test
  (a together provider on an openrouter.ai URL gets the together shape).
  Explicit-only variants (`qwen`, `qwen-chat-template`, `string-thinking`; no
  rung in openai-completions.ts:1126-1136) need only the request-shape `elif` in
  `provider_construction`, no resolver change; set `compat={thinkingFormat: ...}`
  in test specs. `ant-ling` has a rung and needs the precedence test.
- **Check each branch's value expression for a `?? level` fallback.**
  deepseek/together/openrouter/string-thinking use
  `model.thinkingLevelMap?.[level] ?? level`; `ant-ling`
  (openai-completions.ts:581-585) does a raw lookup with no fallback and emits
  nothing when off or unset. pipy `reasoning_value` falls back to the raw level,
  so a no-fallback branch needs its own lookup helper and a no-map test.
- **Three `off` states.** For branches mirroring
  `model.thinkingLevelMap?.off !== null` plus `?? "none"`: a missing `off` emits
  `"none"`, a string emits the string, explicit `off: null` suppresses the
  field. Gate with key membership; `dict.get("off")` conflates missing and
  `None`.
- **Default-branch leak.** `map_thinking_level` keys off map keys and ignores
  `model.reasoning`, so a branch gated `and bool(spec.reasoning)` falls through
  to the default `reasoning_effort` for a non-reasoning model with a
  `thinkingLevelMap`. Let the branch consume all its `thinking_format` cases,
  check reasoning inside the value helper, and test that neither field is
  emitted. zai/together/qwen still carry this latent divergence.
- **New stored levels clamp per provider.** A stored level such as `max` that
  survives a model switch needs a provider-scoped clamp-then-map where Pi clamps
  (`clampThinkingLevel`, openai-codex-responses.ts:468), not a global clamp:
  `max` on a Codex model without `max` becomes `xhigh`, or `high` when neither is
  mapped. Effort is omitted only for unset/off.
- **Available levels are not map keys.** Pi `getSupportedThinkingLevels`
  (models.ts:410-419) treats unmapped ordinary levels as identity-supported and
  requires only `xhigh`/`max` to be mapped; explicit `None` removes a level. Use
  an `available_thinking_levels` helper with those semantics for cycles and
  clamps, not `supported_thinking_levels`. A hand-authored row whose Pi map is
  partial must spell out the identity levels (`gpt-6-sol`
  `{xhigh, max, minimal:low}` needs explicit `low`/`medium`/`high`).

## Rendering Pi's own code under Node

Node 26 runs Pi's TypeScript with
`node --import <pi-mono>/packages/coding-agent/src/experimental/source-resolver.ts script.mts`.

- **TUI components.** Call `initTheme('dark')` and
  `setKeybindings(new KeybindingsManager())` (`core/keybindings.ts`), pass a
  stub runtime/TUI (`{requestRender(){}}`), feed keys through `handleInput`,
  and pin the stripped rows as a test fixture. F7 caught every column width,
  blank row and hint text this way without PTY iterations. Then check colours
  per cell in tmux with Pi forced to `{"theme":"dark"}` in
  `PI_CODING_AGENT_DIR/settings.json`; Pi's default system theme emits
  16-colour codes.
- **Palette.** Generate Pi's exact SGR codes from its own `theme.ts`
  (`loadThemeFromPath(dark.json, 'truecolor'|'256color')`, then `theme.fg`/`bg`
  per token) instead of converting okhsl by hand, and compare PTY captures row
  by row on text plus every span's fg/bg/bold. The theme is a render input:
  styled rows kept in history must restyle on Ctrl+O and resize, not cache
  palette-baked strings.

## Per-turn telemetry and session totals

- Declare per-turn metadata on `AgentAssistantMessage` (usage, provider, model)
  with `compare=False`: the loop, retained history and many tests use history
  equality, and telemetry must not change it.
- A new JSON key still breaks exact-dict tests. Grep tests for
  `"stopReason": "stop"` (event adapters, automation events, retry visibility,
  aborted turn) and add the key; never loosen the assertion.
- Pi `calculateCost` computes `(rate / 1e6) * tokens` per part, which moves
  float totals by one ulp. Write the expectation with the same formula, not
  `approx`.
- Pi session totals (`getSessionStats`, footer, `/session`) iterate
  `sessionManager.getEntries()`: every entry of the file on every branch, not
  the active branch or the live context. In pipy sum
  `NativeSessionTree.get_entries()` (guarded, returns a copy) and bind the footer
  to the live tree (`lambda: product.ctl.session_tree`) so `/new`, `/resume` and
  `/fork` are followed. pipy composes the footer on events, so Pi's footer cache
  is not needed.

## Pi TUI components as inline-scrollback rows

Printed scrollback rows cannot change. Before coding, list every input that
changes the drawing: result arrival, Ctrl+O, terminal width, async callbacks.

- Keep a running row live until it settles, retain its inputs, and re-render
  on expand and on resize (Pi re-renders at the new width). TOOLS3 code review
  round 1 caught width-baked rows: collapsed bash counts wrapped lines, and
  extension renderers receive a width.
- Run Pi async work (the `computeEditsDiff` preview) on a worker thread and
  apply the result under the paint lock with a bounded join. Never run it on the
  loop thread: a FIFO path blocked the UI and the interrupt keys.

## Line ceilings and audit pins

Check the pins in `tests/test_architecture_quality_gates.py` and
`tests/test_god_file_decomposition_final_audit.py` before editing a native
module.

- `src/pipy_harness/native/session.py` sits exactly at its ceiling (the audit
  asserts equality), so edits there keep the line count unchanged; a slice
  that shrinks it lowers both pins. The tool-loop prompt text lives in
  `system_prompt_sections.py` (Pi's `system-prompt.ts`).
- A new `CodingSession` field keeps `coding/session.py` at its 399-line ceiling
  (offset the added lines) and updates three audit pins: `MEMBER_LIST`, the
  synthetic `CodingSession` fixture, and the field-count parameter.
- Removing a message field needs a grep of `scripts/parity_checks` for
  `getattr`-style access, which fails silently instead of raising.

## Resource guarantees of a Python stand-in

A Python fallback for a native tool must keep the tool's resource guarantees,
not only its output. The TOOLS1 reviewer flagged whole-file reads with no size
cap, and an in-process `re` search that could not stop a backtracking pattern.
Run the fallback as a child process that streams the same JSON as the real
binary. One reader then handles both engines, kills the child at the result
limit, and kills it on `cancel_event`. Read files line by line.
