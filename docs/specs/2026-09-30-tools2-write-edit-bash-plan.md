# TOOLS2 + READ2b: Pi's `write`, `edit`, `bash` results, `!` output, tool headers

Status: implemented (TOOLS2 + READ2b in the `docs/backlog.md` Done table).
Pi reference: `~/src/pi-mono` at `1b347794e`, `packages/coding-agent/src/`:
`core/tools/{write,edit,edit-diff,bash,ls,file-mutation-queue,render-utils}.ts`,
`core/tools/renderers/{grep,find,ls,write,edit,bash}.ts`,
`core/bash-executor.ts`, `core/messages.ts` (`bashExecutionToText`),
`modes/interactive/components/bash-execution.ts`, `utils/{text,ansi,shell}.ts`,
`node_modules/diff` 8.0.4 (`libesm/diff/{base,line}.js`).

## Security boundary check

`docs/harness-spec.md`, `docs/pi-parity.md`, `docs/session-storage.md` and
`docs/usage.md` describe the workspace-only mutation policy only as the current
implementation ("stay inside the workspace (READ2b)"). The approval/sandbox
policies in `harness-spec.md` (explicit file excerpt tool, patch apply) belong
to the compatibility runtime, not to the model-driven tools. `bash` is a real
shell that can already write anywhere. No documented boundary requires the
divergence, so `write` and `edit` match Pi. The allowlisted
`command_sandbox.py` (RPC `bash`) keeps its own policy and is not touched.

## `write` (Pi `write.ts`)

- Schema: `path` ("Path to the file to write (relative or absolute)"),
  `content` ("Content to write to the file"); both required.
- Description: "Write content to a file. Creates the file if it doesn't exist,
  overwrites if it does. Automatically creates parent directories."
- Path: `resolve_to_cwd(path, cwd)` (READ2's port), no deny list, no size cap,
  no containment. `mkdir -p dirname`, then write UTF-8 (no newline
  translation). Success text: `Successfully wrote to {path}` (the raw
  argument). Errors are the thrown message with no `write error:` prefix
  (Pi's `createErrorToolResult(error.message)`). Filesystem errors use Node's
  message shape `{CODE}: {libuv text}, {syscall} '{path}'` for the common codes
  (ENOENT, EACCES, EISDIR, ENOTDIR, EEXIST, EROFS, ENOSPC, EPERM), else
  `str(exc)`. A resolver `ValueError` (bad `file://` URL) is an error result.
- Abort: checked before mkdir, between mkdir and write, and after the write:
  `Operation aborted` (Pi `throwIfAborted`). The loop's own cancellation
  observation still wins, as for every tool.
- `withFileMutationQueue` is ported (`tools/file_mutation_queue.py`): the
  executor's bounded cancellation join can leave an abandoned worker inside a
  filesystem call while the next `write`/`edit` starts, so both tools hold a
  per-file lock for their whole mutation. The key is `realpath` of the
  resolved path, or the resolved path itself when it (or a parent) does not
  exist (Pi's ENOENT/ENOTDIR rule); other `realpath` errors propagate as the
  tool's error. Registration is guarded by one module lock and an entry is
  dropped when its last holder releases it, as in Pi.
- Side output (the TUI row pipy commits through `stderr_sink`): the written
  content as Pi's expanded write call shows it (`\r` removed, tabs as three
  spaces, trailing empty lines trimmed). Replaces the unified "new file" diff.

## `edit` (Pi `edit.ts`, `edit-diff.ts`)

- Schema: `path` ("Path to the file to edit (relative or absolute)"), `edits`
  (array of `{oldText, newText}` objects, Pi's descriptions verbatim). The
  `old_string`/`new_string`/`replace_all` shape is removed (no-deprecation).
- Description and error texts: Pi's, verbatim.
- `prepareArguments` (Pi, run before schema validation by the agent loop): an
  `edits` JSON string is parsed (array kept; a single `{oldText,newText}`
  object wrapped), a bare `edits` object is wrapped, and legacy top-level
  `oldText`/`newText` strings are appended to `edits` and removed. pipy adds an
  optional `prepare_arguments(raw) -> raw` hook on a tool, called by
  `ToolExecutor` before `validate_arguments` (the only executor change).
- Validation: empty `edits` → `Edit tool input is invalid. edits must contain
  at least one replacement.`
- Access: the file must be readable and writable (`os.access(R_OK|W_OK)`),
  else `Could not edit file: {path}. Error code: {CODE}.` (ENOENT, EACCES, …;
  Pi reads `error.code`). A directory passes `access` in Pi and then fails on
  read with EISDIR; pipy reports the read's Node-shaped error the same way.
- Read bytes, decode UTF-8 with replacement (Node `toString("utf-8")`), split
  a leading BOM (`﻿`), detect the line ending (first `\r\n` before the
  first `\n` → CRLF), normalize to LF (`\r\n` and lone `\r` → `\n`).
- `applyEditsToNormalizedContent` ported line for line: LF-normalize each
  `oldText`/`newText`; empty `oldText` error; `fuzzyFindText` (exact
  `indexOf`, else `normalizeForFuzzyMatch`: NFKC, per-line `trimEnd`, smart
  quotes, Unicode dashes, special spaces) on the original; when any edit
  needed fuzzy matching, every edit is matched in fuzzy space; not-found,
  duplicate (`countOccurrences` in fuzzy space, `split(...).length - 1`) and
  overlap errors with Pi's single/multi-edit wording; replacements applied in
  reverse; fuzzy results written back through
  `applyReplacementsPreservingUnchangedLines` (only touched lines take the
  normalized text); identical result → Pi's no-change error.
  JS `trimEnd` trims Unicode whitespace like Python `rstrip()` plus U+FEFF;
  the port uses Pi's JS whitespace set.
- Write `bom + restoreLineEndings(newContent, ending)`, success text
  `Successfully replaced {n} block(s) in {path}.`
- Abort checks as in Pi (`Operation aborted`).
- Side output: Pi's `generateDiffString(base, new)` display diff (`+NN line`,
  `-NN line`, ` NN line`, ` … ...`, four context lines, padded line numbers)
  over a port of jsdiff 8.0.4 `diffLines` (Myers with jsdiff's diagonal
  choice, tokenization keeping line endings, component merging). Replaces the
  unified diff. `generateUnifiedPatch`/`details.patch` and
  `firstChangedLine` have no consumer in pipy (no tool-result details channel),
  so they are not ported.

## `bash` result (Pi `bash.ts`)

- Success, exit 0: the output (with Pi's truncation notice) or `(no output)`.
- Non-zero exit: `is_error=True`, text `{output}\n\nCommand exited with code N`
  (`appendStatus`: no leading blank lines when the output is empty, and
  `(no output)` is used as the output text first, exactly as Pi's
  `formatOutput(snapshot)` default). A signal-killed shell reports `128 + n`
  (Python's negative `returncode` mapped).
- Timeout: `is_error=True`, `{output}\n\nCommand timed out after {timeout}
  seconds` with `formatOutput(snapshot, "")` (empty output → just the
  status line).
- Cancel (`cancel_event`): `{output}\n\nCommand aborted`, error.
- Missing working directory: `Working directory does not exist: {cwd}\nCannot
  execute bash commands.` (checked before spawning).
- The module docstring and `_shape` go; `exit code: N` / `[output]` framing is
  removed everywhere (tool, parity check, tests).

## `!` / `!!` shortcut (Pi `bash-executor.ts`, `bashExecutionToText`,
`BashExecutionComponent`)

- Store (`executeBashWithOperations`): each chunk is decoded (streaming UTF-8),
  `stripAnsi` (Pi's `ansi-regex` pattern, ported), `sanitizeBinaryOutput`
  (drop `[\x00-\x08\x0B\x0C\x0E-\x1F￹-￻]`) and `\r` removed; a
  rolling list of chunks keeps at most `2 × 50 KB` of text measured in UTF-16
  code units like Pi's `string.length` (dropping whole chunks from the front
  while more than one remains); once the raw byte total passes
  50 KB a temp file `pipy-bash-<16 hex>.log` (owner-only, as the tool's) gets
  every sanitized chunk. At the end `truncateTail(joined)`; truncated → the
  temp file is ensured. The sink receives the sanitized chunks.
- Result: `output` (tail or full), `exit_code` (None when cancelled; signals
  as `128 + n`), `cancelled`, `truncated`, `full_output_path`.
- Timeout: Pi has none. pipy drops the 600 s deadline on the TTY path (Escape
  cancels, as in Pi). The captured-stream path (no terminal, so no cancel key)
  keeps a 600 s bound and records a deadline kill as cancelled. Retained
  divergence, documented.
- Context record = Pi `bashExecutionToText` exactly: ``Ran `{cmd}` `` newline,
  a fenced block of the output or `(no output)`, then `\n\n(command
  cancelled)` or `\n\nCommand exited with code N`, then `\n\n[Output
  truncated. Full output: {path}]`. The pipy-only "I ran a shell command in
  the workspace (not a tool call)" record is removed; the restored-history
  parser reads Pi's shape (strict: the whole text must match it).
- TUI rows (`BashExecutionComponent.updateDisplay`, logical lines): `$ cmd`,
  then the tail-truncated output (collapsed: last 20 lines), then the status
  lines: `... N more lines (ctrl+o to expand)` / `(ctrl+o to collapse)` when
  lines are hidden, `(cancelled)` or `(exit N)`, and `Output truncated. Full
  output: {path}`. The row follows Ctrl+O like Pi's `setExpanded` (a retained
  collapsed/expanded state). The captured-stream path prints only the status
  lines (the body already streamed).
- The RPC `bash` command keeps the allowlisted sandbox path (separate gap).

## Tool-row headers (Pi `renderers/*.ts`, `render-utils.ts`)

Both header paths (`_plain_tool_call_header` for the TUI and the captured
renderer's `_format_pi_call_header_rich`):

- `grep /{pattern}/ in {shortenPath(path || ".")}` + ` ({glob})` when `glob`
  is a non-empty string + ` limit {limit}` when `limit` is present.
- `find {pattern} in {shortenPath(path || ".")}` + ` (limit {limit})`.
- `ls {shortenPath(path || ".")}` + ` (limit {limit})` (Pi always shows the
  path; pipy's bare `ls` goes).
- `write {path}` / `edit {path}` with `shortenPath`, `...` for an empty path,
  `file_path` accepted as Pi's renderers do.
- Pi's `str()`: a string stays, a missing or `null` value becomes `""` (so the
  `.`/`...` fallbacks apply), and any other type shows `[invalid arg]`.
- `shortenPath`: a path starting with the home directory string becomes
  `~` + rest (a plain string prefix, as in Pi).
- `read` keeps READ1's header (Pi's compact docs/skill classification is a
  separate renderer gap). The TUI's `$ ` prompt on non-bash rows and Pi's
  per-tool result bodies (grep first 15 lines, write hides success, edit diff
  box, bash `Full output` footer) stay as they are: follow-on TOOLS3.

## `ls` order (Pi `ls.ts:109`)

Pi sorts `a.toLowerCase().localeCompare(b.toLowerCase())` (Node ICU, root
collation). Measured on Node (ICU 78.3): `" _-,;:!?.'\"()[]{}@*/\\&#%\`^+<=>|~$"`
then digits then letters; accents are secondary (`a < á < ä < "a b"`), other
symbols (emoji) sit between punctuation and digits, Greek after Latin, CJK
last; no numeric collation (`10 < 9`); `ss < ß`, `ae < æ`, `áe < æ`.

The standard library has no ICU collator, so `tools/collation.py` embeds a
three-level table generated from Node's `Intl.Collator` (sensitivities
`base`/`accent`/full) over printable ASCII, Latin-1, Latin Extended-A/B,
General Punctuation, currency, Greek and Cyrillic (495 characters, 330
primary ranks), plus the characters ICU weighs as sequences: letters with an
extra secondary element (`ß`, `æ`, `œ`, ...) and compatibility forms that
differ at the tertiary level (`ĳ`, `…`, `‼`). Outside the table: other
decimal digits weigh as the ASCII digit, accented letters as their NFD base
with a secondary after the table's, compatibility forms (fullwidth `ａ`,
circled `①`) as a tertiary variant of their NFKD, katakana as a tertiary
variant of hiragana, other letters after the table in ICU's script order
(measured on Node by sorting one letter per Unicode script name: ... Hangul,
Hiragana, Katakana, Bopomofo, Yi, Lisu, then Han last) and by code point
within a script, other symbols just before the currency signs; format
characters are ignorable and a combining mark adds a secondary weight. A
generated random corpus (12 × 600 names over ASCII, accents, expansions,
punctuation, currency, Greek, Cyrillic, Hangul, kana, CJK, fullwidth,
circled digits and emoji) matched Node on every adjacent pair but one (a
stacked double accent). Exact ICU needs ICU itself (PyICU links the C
library; pipy's runtime is stdlib plus `websockets`), so the remaining gap is
a documented approximation: characters outside the table can order
differently from Node inside one script. Python's stable sort keeps directory
order for full ties, as JS's does. The unit test pins a Node-generated
fixture that includes the reviewer's `ａ`/`z` and `가`/`一` cases.

## Global git excludes (`core.excludesFile`)

The `ignore` crate (rg, fd), measured on rg 15.2.0 / fd 10.5.0 with a
scratch `HOME`:

- Path: `~/.gitconfig`'s `excludesfile = …` (any section, case-insensitive,
  lazy regex `^\s*excludesfile\s*=\s*(.+)\s*$`, every `~` replaced by the home
  directory), else `$XDG_CONFIG_HOME/git/config` (or `~/.config/git/config`),
  else `$XDG_CONFIG_HOME/git/ignore` (or `~/.config/git/ignore`).
- Applies where `.gitignore` applies: inside a repository, and everywhere in
  fd's `--no-require-git` walk; not in rg outside a repository.
- Lowest precedence (below `info/exclude`).
- Rooted at the process cwd (pipy: the workspace root, which is the cwd rg
  runs in): a path under the root is matched relative to it (a plain string
  prefix strip, then one leading `/`), any other path is matched whole, so
  unanchored lines apply everywhere and anchored lines only below the root.
  Measured: from `repo/sub`, `/anch.txt` hid `sub/anch.txt`, and the string
  prefix made `repo/subd` read as `d`.

`WalkIgnore.for_search` gains the cwd and loads these rules once. The
differential tests set `HOME`/`XDG_CONFIG_HOME` for both sides and add a
global excludes file.

## Docs

`CHANGELOG.md` `[Unreleased]`; `docs/backlog.md` (READ2b/TOOLS2 entries →
Done rows, new TOOLS3 follow-on); `docs/pi-parity.md` rows 92/105/117;
`docs/usage.md` (write/edit paths); the `session.py` system prompt tool lines
(same line count); module docstrings; `docs/parity-plan.md` /
`docs/pi-mono-gap-audit.md` mentions if any.

## Done when

- Unit tests per tool: write (overwrite, parents, absolute/`~` path, errors),
  edit (multi-edit, fuzzy, BOM/CRLF, every error text, prepare_arguments
  shapes, diff string incl. jsdiff tie cases), bash (exit/timeout/abort texts,
  signal code), `!` store/record/rows, headers, ls order, global excludes
  differential.
- `just check`, every `scripts/parity_checks/*.py`, `scripts/parity_score.sh`
  green; a live openai-codex run edits a file, overwrites one and runs a
  failing command.
