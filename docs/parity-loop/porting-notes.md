# Parity-Loop Porting Notes

Reference notes for the parity loop's Phase 2 plan (`skill-body.md`): measured
behavior of external tools and Node builtins that Pi relies on and pipy
emulates, plus porting patterns that cost review rounds. Pin each rule that
applies in the plan before review.

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

## Resource guarantees of a Python stand-in

A Python fallback for a native tool must keep the tool's resource guarantees,
not only its output. The TOOLS1 reviewer flagged whole-file reads with no size
cap, and an in-process `re` search that could not stop a backtracking pattern.
Run the fallback as a child process that streams the same JSON as the real
binary. One reader then handles both engines, kills the child at the result
limit, and kills it on `cancel_event`. Read files line by line.
